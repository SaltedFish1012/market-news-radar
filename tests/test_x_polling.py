import asyncio
import httpx
from test_x_pipeline import make_engine


def test_first_snapshot_and_five_page_batch_resume(tmp_path):
    from app.x_news import XClient,poll_account
    engine=make_engine(tmp_path);requests=[]
    def respond(request):
        assert request.url.host=='api.x.com' and request.headers['Authorization']=='Bearer SECRET'
        if '/by/username/' in request.url.path:
            return httpx.Response(200,json={'data':{'id':'42','username':'Example','name':'Author'}})
        requests.append(request)
        assert 'note_post' in request.url.params['post.fields'] and 'tweet.fields' not in request.url.params
        if request.url.params['max_results']=='10':
            return httpx.Response(200,json={'data':[{'id':'10','text':'initial','author_id':'42'}],'meta':{'next_token':'ignored'}})
        token=request.url.params.get('pagination_token','1');n=int(token)
        return httpx.Response(200,json={'data':[{'id':str(100+n),'text':'next','author_id':'42'}],
                                       'meta':{'next_token':str(n+1)} if n<6 else {}})
    async def run():
        client=XClient('SECRET',httpx.MockTransport(respond));account={'username':'Example','push_mode':'all'}
        await poll_account(engine,client,account,now=1000)
        assert engine.db.x_cursor('42')['since_id']=='10' and not engine.db.due_alerts()
        await poll_account(engine,client,account,now=1001)
        state=engine.db.x_cursor('42');assert state['since_id']=='10' and state['next_token']=='6'
        await poll_account(engine,client,account,now=1002)
        assert engine.db.x_cursor('42')['since_id']=='106'
        await client.close()
    asyncio.run(run());assert len(requests)==7


def test_failed_page_and_invalid_token_do_not_advance(tmp_path):
    from app.x_news import XClient,poll_account,XAPIError
    engine=make_engine(tmp_path);engine.db.save_x_cursor('42',{'initialized':True,'since_id':'10','next_token':'invalid','batch_since_id':'10','batch_max_id':'100','query_fingerprint':'old'})
    def respond(request):
        if '/by/username/' in request.url.path:return httpx.Response(200,json={'data':{'id':'42','username':'Example'}})
        return httpx.Response(200,json={'data':[{'id':'20','text':'one','author_id':'42'}],'errors':[{'detail':'SECRET'}]})
    async def run():
        client=XClient('SECRET',httpx.MockTransport(respond))
        try:
            await poll_account(engine,client,{'username':'Example'},now=1000)
            assert False
        except XAPIError as error: assert 'SECRET' not in str(error)
        assert engine.db.x_cursor('42')['since_id']=='10' and not engine.db.list_rows('news')
        await client.close()
    asyncio.run(run())


def test_parse_long_text_references_and_old_aliases():
    from app.x_news import parse_posts
    data={'data':[{'id':'101','author_id':'42','text':'short','note_tweet':{'text':'<b>&amp;</b>'+'a'*8000},
                  'edit_history_tweet_ids':['100','101'],'referenced_tweets':[{'id':'9','type':'quoted'}]}],
          'includes':{'users':[{'id':'42','username':'Renamed','name':'Current'},{'id':'8','username':'Original','name':'Original author'}],
                      'tweets':[{'id':'9','author_id':'8','text':'original speech'}]}}
    p=parse_posts(data,{'id':'42','username':'Old'})[0]
    assert p['username']=='Renamed' and len(p['raw_text'])>8000
    assert p['references'][0]['username']=='Original' and p['edit_history_ids']==['100','101']


def test_archive_failure_replays_the_page(tmp_path):
    from app.x_news import XClient,poll_account
    engine=make_engine(tmp_path);engine.db.save_x_cursor('42',{'initialized':True,'since_id':'10'})
    def respond(request):
        if '/by/username/' in request.url.path:return httpx.Response(200,json={'data':{'id':'42','username':'Example'}})
        return httpx.Response(200,json={'data':[{'id':'20','text':'one'},{'id':'19','text':'two'}]})
    original=engine.ingest_x;calls=[]
    async def fail_second(post,account,**kwargs):
        calls.append(post['post_id'])
        if len(calls)==2:raise OSError('disk')
        return await original(post,account,**kwargs)
    async def run():
        client=XClient('SECRET',httpx.MockTransport(respond));engine.ingest_x=fail_second
        try:await poll_account(engine,client,{'username':'Example'},now=1000)
        except OSError:pass
        assert engine.db.x_cursor('42')['since_id']=='10'
        engine.ingest_x=original;await poll_account(engine,client,{'username':'Example'},now=1001)
        assert engine.db.x_cursor('42')['since_id']=='20' and len(engine.db.list_rows('news'))==2
        await client.close()
    asyncio.run(run())


def test_latest_post_time_never_moves_backwards_or_disappears(tmp_path):
    from app.x_news import XClient,poll_account
    engine=make_engine(tmp_path);engine.db.save_x_cursor('42',{'initialized':True,'since_id':'10'})
    rounds=iter([
        {'data':[{'id':'30','text':'new','created_at':'2026-10-12T10:00:00Z'}],'meta':{'next_token':'older'}},
        {'data':[{'id':'20','text':'older','created_at':'2026-10-11T10:00:00Z'}]},
        {'data':[{'id':'31','text':'invalid','created_at':'not a date'}]},
        {'data':[{'id':'32','text':'missing'}]},
        {'data':[{'id':'33','text':'offset','created_at':'2026-10-12T19:00:00+09:00'},
                 {'id':'34','text':'newest','created_at':'2026-10-12T11:00:00Z'}]},
        {'data':[]},
    ])
    def respond(request):
        if '/by/username/' in request.url.path:return httpx.Response(200,json={'data':{'id':'42','username':'Example'}})
        return httpx.Response(200,json=next(rounds))
    async def run():
        client=XClient('SECRET',httpx.MockTransport(respond));account={'username':'Example'}
        for now in range(1000,1003):
            await poll_account(engine,client,account,now=now)
            assert engine.db.x_cursor('42')['last_post_at']=='2026-10-12T10:00:00Z'
        await poll_account(engine,client,account,now=1003)
        assert engine.db.x_cursor('42')['last_post_at']=='2026-10-12T11:00:00Z'
        await poll_account(engine,client,account,now=1004)
        assert engine.db.x_cursor('42')['last_post_at']=='2026-10-12T11:00:00Z'
        assert engine.db.x_cursor('42')['since_id']=='34'
        await client.close()
    asyncio.run(run())
