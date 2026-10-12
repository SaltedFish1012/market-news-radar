from __future__ import annotations
import asyncio, json, logging, re, time
from datetime import datetime, timezone, timedelta
from collections import deque
from html import unescape
from urllib.parse import quote
import httpx

from .db import DB, utcnow
from .ai_client import request_analysis
from .feeds import parse_dt, strip_html
from .signals import classify, event_hash, norm_symbol, PriceRules
from .instruments import resolve_instrument
from .market_sessions import Quote, quote_is_eligible, SESSION_NAMES

log=logging.getLogger(__name__)


def readable_time(value):
    if isinstance(value,datetime): return value.astimezone(timezone.utc).isoformat()
    return parse_dt(str(value)) if value else None


class Engine:
    def __init__(self, settings):
        self.settings=settings
        self.db=DB(settings.db_path)
        self.watchlist=settings.read_json('watchlist.json')
        self.rules=PriceRules(self.watchlist)
        self.ai_quota=deque(maxlen=600)
        self.ai_slots=asyncio.Semaphore(2)
        self.tasks=set()
        self.rss_warmups=set()

    def spawn(self, coro):
        task=asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def related_quote_summary(self, symbols: list[str]) -> str:
        rows={r['symbol']:r for r in self.db.list_rows('quotes',500)}
        out=[]
        for s in symbols[:8]:
            r=rows.get(norm_symbol(s))
            if r:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(r['observed_at'])).total_seconds()
                if age<180:
                    out.append(f"{s}: ${r['price']:g} (报价更新 {age:.0f}秒前)")
        return '; '.join(out) if out else '无足够新鲜的实时报价；禁止推测实际涨跌幅'

    async def ingest(self, item: dict, silent: bool = False) -> bool:
        title=strip_html(str(item.get('title','')))[:300]
        if not title:return False
        summary=strip_html(str(item.get('summary','')))[:1500]
        published=readable_time(item.get('published_at'))
        source=str(item.get('source','Unknown'))[:100]
        raw_symbols=[norm_symbol(str(s)) for s in (item.get('source_symbols') or [])]
        raw_symbols=[s for s in raw_symbols if s]
        priority, related, themes=classify(title,summary,raw_symbols,self.watchlist,source)
        received=utcnow()
        # News source ID is distinct from cross-source title dedup. We store separate records.
        raw_id=str(item.get('source_id') or '')
        key=event_hash(title,published,source+'|'+raw_id if raw_id else '')
        record={'event_key':key, 'source':source,'title':title,'summary':summary,'url':str(item.get('url') or '')[:1500],
             'published_at':published,'received_at':received,'source_symbols':raw_symbols,
             'related_symbols':related,'priority':priority}
        news_id=self.db.put_news(record)
        if news_id is None:return False
        if priority=='info' or silent:return True
        # Old records and invalid publication timestamps never trigger 'breaking' notifications.
        if published is None:return True
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(published)).total_seconds()
        if age > self.settings.news_max_age_minutes*60 or age < -300:return True
        caption=('高优先' if priority=='critical' else '关注')
        body=(f'来源：{source}\n消息：{title}\n关联股票（待确认影响）：{", ".join(related[:10]) or "未识别"}\n'
              f'来源发布时间：{published}\n接收时间：{received}\n'
              f'状态：新闻已接收，尚未交叉核验\n{record["url"]}')
        self.db.add_alert(f'news:{news_id}:fast',f'{caption}财经新闻',body,priority,news_id)
        if self.settings.ai_key and getattr(self.settings, 'ai_enabled', True):
            self.spawn(self.analyze(news_id,record))
        return True

    def reserve_ai_request(self, settings=None):
        config = settings or self.settings
        now = time.time()
        while self.ai_quota and now-self.ai_quota[0] > 3600:
            self.ai_quota.popleft()
        limit = getattr(config, 'ai_max_requests_per_hour', 60)
        if len(self.ai_quota) >= limit:
            self.db.health('ai', 'quota', f'{limit}/hour local guard; analysis skipped')
            return False
        self.ai_quota.append(now)
        return True

    async def analyze(self, news_id: int, event: dict):
        if not self.reserve_ai_request():
            return
        async with self.ai_slots:
            try:
                result=await self._request_ai(event)
                self.db.set_ai(news_id,result)
                if not result:return
                tickers=result.get('affected_symbols',[])
                if not isinstance(tickers,list):tickers=[]
                tickers=[norm_symbol(str(t)) for t in tickers[:12] if norm_symbol(str(t))]
                justification=str(result.get('rationale',''))[:700]
                checks=str(result.get('uncertainty',''))[:380]
                sent=str(result.get('sentiment','uncertain'))[:40]
                evidence=str(result.get('evidence_level','unverified'))[:40]
                snapshot=self.related_quote_summary(tickers or event['related_symbols'])
                body=(f'事件：{event["title"]}\n市场影响方向（推断）：{sent}\n'
                      f'可能受影响股票：{", ".join(tickers) or "待核实"}\n'
                      f'依据：{justification}\n证据状态：{evidence}\n注意事项：{checks}\n'
                      f'行情快照：{snapshot}\n原文：{event["url"]}')
                self.db.add_alert(f'news:{news_id}:ai','AI 初步分析（非交易指令）',body,'high',news_id)
                self.db.health('ai','ok',f'last analyzed news {news_id}')
            except Exception as exc:
                log.warning('AI analysis unavailable: %s',exc)
                self.db.health('ai','error',str(exc))

    async def _request_ai(self, event: dict) -> dict:
        return await request_analysis(self.settings, event)

    async def on_quote(self, quote: Quote):
        try:
            instrument = resolve_instrument(quote.symbol, self.watchlist.get('instruments'))
        except ValueError:
            return
        if quote.currency != instrument.currency:
            self.db.health('longbridge_quote','error','报价币种与配置不一致')
            return
        if not self.db.put_session_quote(quote) or not quote_is_eligible(quote):
            return
        for alert in self.rules.feed(instrument.symbol,quote.price,quote.quote_at,session=quote.session):
            tick=int(quote.quote_at/max(1,alert['cooldown_s']))
            stamp=datetime.fromtimestamp(quote.quote_at,timezone.utc).isoformat()
            self.db.add_alert(f'quote:{alert["key"]}:{tick}',f'{instrument.symbol} 价格异动',
                alert['detail']+f'\n币种：{quote.currency}\n时段：{SESSION_NAMES[quote.session]}\n报价时间：{stamp}\n数据来源：{quote.source}。未验证市场原因。', 'high')


async def notification_loop(engine, settings):
    async with httpx.AsyncClient(timeout=12) as client:
        while True:
            if not getattr(settings, 'notifications_enabled', True):
                engine.db.health('notifications', 'disabled', 'Notifications paused in settings')
                await asyncio.sleep(2)
                continue
            pending=engine.db.due_alerts()
            for event in pending:
                try:
                    channels=0
                    title=event['title']
                    body=event['body']
                    if settings.telegram_token and settings.telegram_chat:
                        resp=await client.post(f'https://api.telegram.org/bot{settings.telegram_token}/sendMessage',json={
                             'chat_id':settings.telegram_chat, 'text':f'🚨 {title}\n\n{body}'[:3900],
                             'disable_web_page_preview':True})
                        resp.raise_for_status()
                        data=resp.json()
                        if not data.get('ok'):raise RuntimeError('Telegram returned !ok')
                        channels+=1
                    if settings.bark_key:
                        u=(settings.bark_base+'/'+quote(settings.bark_key,safe='')+'/'+quote(title,safe='')+'/'+quote(body[:1000],safe=''))
                        resp=await client.get(u, params={'group':'MarketNewsRadar','isArchive':'1'})
                        resp.raise_for_status()
                        channels+=1
                    if channels==0:
                        engine.db.health('notifications','disabled','Set Telegram or Bark credentials')
                        break
                    engine.db.alert_result(event['id'],True)
                    engine.db.health('notifications','ok',f'last sent alert {event["id"]}')
                except Exception as exc:
                    # Telegram/Bark place credentials in URLs; never persist exception URLs.
                    code = f' HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else ''
                    message = f'通知发送失败：{type(exc).__name__}{code}，请检查渠道配置或网络'
                    engine.db.alert_result(event['id'],False,message)
                    engine.db.health('notifications','error',message)
            await asyncio.sleep(2)
