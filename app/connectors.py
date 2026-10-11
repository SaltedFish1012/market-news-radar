"""Upstream adapters. All authentication happens locally on user's server."""
from __future__ import annotations
import asyncio, json, logging, os
from datetime import datetime, timezone
from threading import Event
import websockets

log=logging.getLogger(__name__)


def decode_messages(payload):
    raw=json.loads(payload)
    return raw if isinstance(raw,list) else [raw]


async def alpaca_news_loop(engine, settings):
    if not getattr(settings, 'alpaca_news_enabled', True) or not settings.alpaca_key or not settings.alpaca_secret:
        engine.db.health('alpaca_news','disabled','ALPACA_API_KEY / ALPACA_SECRET_KEY missing')
        return
    backoff=3
    headers={'APCA-API-KEY-ID': settings.alpaca_key, 'APCA-API-SECRET-KEY':settings.alpaca_secret}
    while True:
        try:
            engine.db.health('alpaca_news','connecting','')
            async with websockets.connect(settings.alpaca_url,additional_headers=headers,open_timeout=18,ping_interval=20,ping_timeout=20,max_size=6*1024*1024) as socket:
                # Official API accepts credentials in headers, followed by a subscribe action.
                await socket.send(json.dumps({'action':'subscribe','news':['*']}))
                backoff=3
                async for payload in socket:
                    for item in decode_messages(payload):
                        msg_type=item.get('T')
                        if msg_type=='n':
                            await engine.ingest({
                              'source': 'Alpaca / '+str(item.get('source') or 'news'),
                              'source_id':f"alpaca:{item.get('id')}",
                              'title':str(item.get('headline','')),
                              'summary':str(item.get('summary','') or item.get('content',''))[:4000],
                              'url':str(item.get('url','')),
                              'published_at':item.get('created_at'),
                              'source_symbols':item.get('symbols',[]) or []})
                            engine.db.health('alpaca_news','ok','connected; received live news')
                        elif msg_type in ('error',):
                            reason=str(item.get('msg',item))[:300]
                            engine.db.health('alpaca_news','error',reason)
                            log.error('Alpaca news stream error: %s',reason)
                        elif msg_type=='subscription':
                            engine.db.health('alpaca_news','ok','subscription '+str(item.get('news'))[:100])
                        elif msg_type=='success':
                            engine.db.health('alpaca_news','ok',str(item.get('msg','')))
        except asyncio.CancelledError:raise
        except Exception as exc:
            log.warning('Alpaca reconnect in %ss: %s',backoff,exc)
            engine.db.health('alpaca_news','error',str(exc))
            await asyncio.sleep(backoff)
            backoff=min(backoff*2,90)


def lb_config():
    from longbridge.openapi import Config, OAuthBuilder
    client_id=os.getenv('LONGBRIDGE_CLIENT_ID','').strip()
    if client_id:
        # Register and authorize locally first; token cached in mounted home directory.
        oauth=OAuthBuilder(client_id).build(lambda url: log.warning('Authorize Longbridge locally: %s',url))
        return Config.from_oauth(oauth)
    return Config.from_apikey_env()


async def longbridge_quote_loop(engine, settings):
    if not settings.lb_enabled:
        engine.db.health('longbridge_quote','disabled','LB_ENABLED=false')
        return
    try:
        from longbridge.openapi import QuoteContext, SubType
    except ImportError:
        engine.db.health('longbridge_quote','error','Longbridge SDK not installed; install requirements-longbridge.txt')
        return
    loop=asyncio.get_running_loop()
    watchlist=engine.watchlist
    symbols=[s+'.US' for s in watchlist.get('symbols',[]) if not s.endswith('.US')]
    while True:
        stopped = Event()
        try:
            def run(stopped=stopped):
                ctx=QuoteContext(lb_config())
                if stopped.is_set():
                    return None
                def callback(symbol, event):
                    if stopped.is_set():
                        return
                    value=getattr(event,'last_done',None)
                    if value is None:return
                    try:price=float(value)
                    except (TypeError, ValueError):return
                    future=asyncio.run_coroutine_threadsafe(engine.on_quote(symbol,price),loop)
                    def observe(f):
                        try:f.result()
                        except Exception as e:log.warning('Quote callback: %s',e)
                    future.add_done_callback(observe)
                ctx.set_on_quote(callback)
                ctx.subscribe(symbols, [SubType.Quote], is_first_push=True)
                return ctx
            ctx=await asyncio.to_thread(run)
            engine.db.health('longbridge_quote','ok',f'{len(symbols)} subscribed symbols')
            # Keep QuoteContext alive while its SDK's own connection handles pushes.
            while True:
                await asyncio.sleep(30)
                # Read is intentionally lightweight; SDK may implement reconnect internally.
                _=ctx
        except asyncio.CancelledError:
            stopped.set()
            raise
        except Exception as exc:
            stopped.set()
            engine.db.health('longbridge_quote','error',str(exc))
            log.warning('Longbridge quote restart after error: %s',exc)
            await asyncio.sleep(20)


async def longbridge_news_loop(engine, settings):
    if not settings.lb_news_enabled:
        engine.db.health('longbridge_news','disabled','LB_NEWS_ENABLED=false')
        return
    # Independent per-symbol polling. No news push subscription is documented here.
    try:
        from longbridge.openapi import ContentContext
    except ImportError:
        engine.db.health('longbridge_news','error','Longbridge SDK not installed; install requirements-longbridge.txt')
        return
    symbols=engine.watchlist.get('symbols',[])[:25]
    ctx=None
    while True:
        try:
            if ctx is None:ctx=await asyncio.to_thread(lambda:ContentContext(lb_config()))
            for s in symbols:
                items=await asyncio.to_thread(ctx.news,s+'.US')
                for news in (items or [])[:20]:
                    await engine.ingest({'source':'Longbridge News',
                       'source_id':f'lb:{news.id}', 'title':str(news.title),
                       'summary':str(getattr(news,'description','') or ''),
                       'url':str(getattr(news,'url','') or ''),
                       'published_at':getattr(news,'published_at',None),
                       'source_symbols':[s]})
                await asyncio.sleep(0.4)
            engine.db.health('longbridge_news','ok',f'checked {len(symbols)} symbols')
        except asyncio.CancelledError:raise
        except Exception as exc:
            ctx=None
            engine.db.health('longbridge_news','error',str(exc))
            log.warning('Longbridge news error %s',exc)
        await asyncio.sleep(settings.lb_news_interval)
