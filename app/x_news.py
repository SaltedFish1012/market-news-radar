"""Read-only X API timelines with durable page progress."""
import asyncio
import hashlib
import json
import re
import time
import math
from email.utils import parsedate_to_datetime
from datetime import datetime
import httpx
from .settings_store import XAccount


class XAPIError(Exception):
    def __init__(self, kind, status_code=None, retry_at=None):
        self.kind,self.status_code,self.retry_at = kind,status_code,retry_at
        super().__init__(f'X API：{kind}' + (f'（HTTP {status_code}）' if status_code else ''))


def retry_delay(error, failures, now):
    if error.retry_at is not None and math.isfinite(error.retry_at):
        return max(0,error.retry_at-now)
    if error.kind in ('authentication','permission','billing'):
        return 300
    initial,cap = (30,900) if error.kind == 'rate_limit' else (5,300)
    return min(cap,initial*2**min(20,max(0,failures-1)))


def retry_timestamp(headers, now):
    values = []
    raw = headers.get('Retry-After')
    if raw:
        try: values.append(now+max(0,float(raw)))
        except ValueError:
            try: values.append(parsedate_to_datetime(raw).timestamp())
            except (ValueError,TypeError,OverflowError): pass
    try: values.append(float(headers.get('x-rate-limit-reset','nan')))
    except ValueError: pass
    valid = [v for v in values if math.isfinite(v) and v>now]
    return max(valid,default=None)


def identity(value):
    value = str(value)
    if not re.fullmatch(r'[0-9]{1,19}',value):
        raise XAPIError('响应身份无效')
    return value


class XClient:
    def __init__(self, token, transport=None):
        self.http = httpx.AsyncClient(base_url='https://api.x.com',timeout=20,transport=transport,
                                      headers={'Authorization':'Bearer '+token},follow_redirects=False)

    async def close(self):
        await self.http.aclose()

    async def get(self, path, params=None):
        try:
            response = await self.http.get(path,params=params)
        except httpx.HTTPError as exc:
            raise XAPIError('network') from exc
        try:
            data = response.json()
        except ValueError:
            if response.status_code == 200:
                raise XAPIError('invalid_response',200)
            data = {}
        if response.status_code != 200:
            kind = {401:'authentication',403:'permission',404:'not_found',402:'billing',429:'rate_limit'}.get(response.status_code,'upstream')
            if response.status_code == 400 and params and params.get('pagination_token') and 'pagination' in json.dumps(data).lower():
                kind = 'invalid_page_token'
            raise XAPIError(kind,response.status_code,retry_timestamp(response.headers,time.time()) if kind=='rate_limit' else None)
        if not isinstance(data,dict) or data.get('errors'):
            raise XAPIError('partial_response',response.status_code)
        return data

    async def resolve_user(self, username):
        username = XAccount(username=username).username
        data = await self.get('/2/users/by/username/'+username,{'user.fields':'id,name,username'})
        return self.user(data)

    async def resolve_user_by_id(self, user_id):
        return self.user(await self.get('/2/users/'+identity(user_id),{'user.fields':'id,name,username'}))

    @staticmethod
    def user(data):
        user = data.get('data')
        if not isinstance(user,dict) or not user.get('username'):
            raise XAPIError('partial_response')
        return {'id':identity(user['id']), 'username':XAccount(username=user['username']).username,
                'name':str(user.get('name',''))}

    async def fetch_posts(self, user_id, account, *, since_id=None, next_token=None, max_results=100):
        params = {'post.fields':'created_at,text,note_post,edit_controls',
                  'expansions':'author_id,referenced_posts,edit_history_post_ids',
                  'user.fields':'id,name,username','max_results':max_results}
        if since_id: params['since_id'] = identity(since_id)
        if next_token: params['pagination_token'] = next_token
        exclude = []
        if not account.get('include_replies'): exclude.append('replies')
        if not account.get('include_reposts'): exclude.append('retweets')
        if exclude: params['exclude'] = ','.join(exclude)
        return await self.get('/2/users/'+identity(user_id)+'/tweets',params)


def parse_posts(data, user):
    if not isinstance(data.get('data',[]),list): raise XAPIError('partial_response')
    includes = data.get('includes') or {}
    users = {str(u['id']):u for u in includes.get('users',[])}
    references = {str(p['id']):p for p in includes.get('posts',includes.get('tweets',[]))}
    result = []
    for row in data.get('data') or []:
        post_id = identity(row.get('id'))
        author_id = identity(row.get('author_id',user['id']))
        author = users.get(author_id,user if author_id == user['id'] else None)
        if not author or 'text' not in row: raise XAPIError('partial_response')
        username = XAccount(username=author['username']).username
        refs = []
        for ref in row.get('referenced_posts',row.get('referenced_tweets',[])) or []:
            ref_id = identity(ref['id'])
            original = references.get(ref_id,{})
            original_author = users.get(str(original.get('author_id')), {})
            refs.append({'post_id':ref_id,'type':ref['type'],'author_id':original.get('author_id'),
                         'username':original_author.get('username'),'author_name':original_author.get('name'),
                         'raw_text':(original.get('note_post') or original.get('note_tweet') or {}).get('text',original.get('text',''))})
        result.append({'post_id':post_id,'author_id':author_id,'username':username,'author_name':author.get('name',''),
                       'raw_text':str((row.get('note_post') or row.get('note_tweet') or {}).get('text',row['text'])),
                       'created_at':row.get('created_at'),'edit_history_ids':[identity(v) for v in
                           row.get('edit_history_post_ids',row.get('edit_history_tweet_ids',[post_id]))],
                       'references':refs})
    return result


async def poll_account(engine, client, account, *, now):
    cached = engine.db.x_user(account['username'])
    try:
        user = await client.resolve_user(account['username'])
    except XAPIError as exc:
        if exc.kind != 'not_found' or not cached: raise
        user = await client.resolve_user_by_id(cached['id'])
    await asyncio.sleep(0)
    engine.db.save_x_user(user,account['username'])
    state = engine.db.x_cursor(user['id'])
    initial = not state.get('initialized')
    fingerprint = hashlib.sha256(json.dumps(account,sort_keys=True).encode()).hexdigest()
    if state.get('query_fingerprint') != fingerprint:
        state.update(query_fingerprint=fingerprint,next_token=None,batch_since_id=state.get('since_id'),batch_max_id=state.get('since_id'))
        engine.db.save_x_cursor(user['id'],state)
    if not state.get('next_token'):
        state.update(batch_since_id=state.get('since_id'),batch_max_id=state.get('since_id'))
    for _ in range(1 if initial else 5):
        try:
            data = await client.fetch_posts(user['id'],account,since_id=None if initial else state.get('batch_since_id'),
                                            next_token=state.get('next_token'),max_results=10 if initial else 100)
        except XAPIError as exc:
            if exc.kind == 'invalid_page_token':
                state['next_token'] = None
                engine.db.save_x_cursor(user['id'],state)
            raise
        posts = parse_posts(data,user)
        maximum = state.get('batch_max_id')
        for post in posts:
            maximum = max(filter(None,[maximum,post['post_id']]),key=int)
            types = {r['type'] for r in post['references']}
            if ('replied_to' in types and not account.get('include_replies')) or ('retweeted' in types and not account.get('include_reposts')):
                continue
            await asyncio.sleep(0)  # Observe cancellation before a synchronous SQLite write.
            await engine.ingest_x(post,account,silent=initial)
        state.update(batch_max_id=maximum,next_token=None if initial else (data.get('meta') or {}).get('next_token'),
                     last_success_at=now,failures=0,retry_at=0,error='')
        latest, latest_at = state.get('last_post_at'), float('-inf')
        for value in [latest, *(post.get('created_at') for post in posts)]:
            try:
                parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
                if parsed.tzinfo is None: continue
                timestamp = parsed.timestamp()
                if math.isfinite(timestamp) and timestamp > latest_at:
                    latest, latest_at = value, timestamp
            except (AttributeError, ValueError, TypeError, OverflowError, OSError):
                continue
        if latest_at != float('-inf'): state['last_post_at'] = latest
        if not state['next_token']:
            state.update(initialized=True,since_id=maximum,batch_since_id=None,batch_max_id=None)
        engine.db.save_x_cursor(user['id'],state)
        if not state['next_token']: break


async def poll_round(engine, client, accounts, *, now):
    for account in accounts:
        if not account.get('enabled',True): continue
        user = engine.db.x_user(account['username'])
        key = user['id'] if user else 'username:'+account['username'].casefold()
        state = engine.db.x_cursor(key)
        if state.get('retry_at',0)>now: continue
        try:
            await poll_account(engine,client,account,now=now)
            engine.db.health('x:'+account['username'],'ok','最近轮询成功；无新帖也视为成功')
        except asyncio.CancelledError: raise
        except Exception as exc:
            error = exc if isinstance(exc,XAPIError) else XAPIError('local_storage_or_response')
            current = engine.db.x_user(account['username'])
            key = current['id'] if current else key
            state = engine.db.x_cursor(key)
            failures = state.get('failures',0)+1
            hints = {'authentication':'Token 无效或已过期','permission':'权限不足或账号受限',
                     'billing':'请在 X 开发者控制台检查额度与账单','rate_limit':'达到读取限流；等待供应商恢复',
                     'network':'网络不可用','not_found':'账号不存在或无法读取'}
            message = hints.get(error.kind,'读取或归档失败，请稍后重试')
            state.update(failures=failures,retry_at=now+retry_delay(error,failures,now),error=message)
            engine.db.save_x_cursor(key,state)
            engine.db.health('x:'+account['username'],'error',message)


async def x_news_loop(engine, settings):
    if not settings.x_enabled or not settings.x_bearer_token:
        engine.db.health('x_news','disabled','X 用户言论监控未启用')
        return
    client = XClient(settings.x_bearer_token)
    engine.db.health('x_news','ready','X 用户言论：按配置账号定期读取')
    try:
        while True:
            started = time.monotonic()
            await poll_round(engine,client,settings.x_accounts,now=time.time())
            await asyncio.sleep(max(0,settings.x_poll_interval-(time.monotonic()-started)))
    finally:
        await client.close()
