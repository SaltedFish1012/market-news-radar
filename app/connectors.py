"""Upstream adapters. All authentication happens locally on user's server."""
from __future__ import annotations
import asyncio, json, logging, os, time
from datetime import datetime, timezone
from threading import Event
import websockets
from .instruments import resolve_instrument, Instrument
from .market_sessions import Quote

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


def longbridge_symbols(watchlist: dict) -> dict[str, str]:
    out = {}
    for symbol in watchlist.get('symbols', []):
        instrument = resolve_instrument(symbol, watchlist.get('instruments'))
        if instrument.market == 'US' and instrument.asset_type != 'index' and instrument.provider_symbols.get('longbridge'):
            out[instrument.provider_symbols['longbridge']] = instrument.symbol
    return out


def _epoch(value):
    if isinstance(value, datetime):
        return value.timestamp() if value.tzinfo is not None else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _enum_name(value):
    return str(getattr(value, 'name', value)).split('.')[-1].lower()


def decode_longbridge_quotes(symbol: str, event: object, instrument: Instrument, received_at: float) -> list[Quote]:
    result = []
    def append(data, session):
        try:
            price = float(data.last_done)
        except (AttributeError, TypeError, ValueError):
            return
        status = _enum_name(getattr(data, 'trade_status', getattr(event, 'trade_status', 'unknown')))
        result.append(Quote(instrument.symbol, price, instrument.currency, 'Longbridge OpenAPI', session,
            _epoch(getattr(data, 'timestamp', None)), received_at, 'normal' if status == 'normal' else status))
    session_value = getattr(event, 'trade_session', None)
    session = {'intraday':'regular','regular':'regular','pre':'pre','post':'post','overnight':'overnight'}.get(_enum_name(session_value),'unknown')
    is_snapshot = session_value is None and any(hasattr(event, name) for name in ('pre_market_quote','post_market_quote','over_night_quote','overnight_quote'))
    append(event, 'regular' if is_snapshot else session)
    for name, label in [('pre_market_quote','pre'),('post_market_quote','post'),('over_night_quote','overnight'),('overnight_quote','overnight')]:
        data = getattr(event, name, None)
        if data is not None:
            append(data, label)
    return result


async def deliver_longbridge_quote(engine, quote: Quote, stopped: Event):
    if not stopped.is_set():
        await engine.on_quote(quote)


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
    mapping = longbridge_symbols(watchlist)
    symbols = list(mapping)
    if not symbols:
        engine.db.health('longbridge_quote','disabled','没有可订阅的美股映射')
        return
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
                    if symbol not in mapping:
                        return
                    instrument = resolve_instrument(mapping[symbol], watchlist.get('instruments'))
                    for quote in decode_longbridge_quotes(symbol,event,instrument,time.time()):
                        future=asyncio.run_coroutine_threadsafe(deliver_longbridge_quote(engine,quote,stopped),loop)
                        def observe(f):
                            try:f.result()
                            except Exception:log.warning('Quote callback unavailable')
                        future.add_done_callback(observe)
                ctx.set_on_quote(callback)
                for snapshot in ctx.quote(symbols):
                    callback(snapshot.symbol, snapshot)
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
            engine.db.health('longbridge_quote','error','长桥连接或授权失败，请检查行情权限与本地认证')
            log.warning('Longbridge quote restart after error')
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
    mapping = longbridge_symbols(engine.watchlist)
    symbols=list(mapping)[:25]
    ctx=None
    while True:
        try:
            if ctx is None:ctx=await asyncio.to_thread(lambda:ContentContext(lb_config()))
            for s in symbols:
                items=await asyncio.to_thread(ctx.news,s)
                for news in (items or [])[:20]:
                    await engine.ingest({'source':'Longbridge News',
                       'source_id':f'lb:{news.id}', 'title':str(news.title),
                       'summary':str(getattr(news,'description','') or ''),
                       'url':str(getattr(news,'url','') or ''),
                       'published_at':getattr(news,'published_at',None),
                       'source_symbols':[mapping[s]]})
                await asyncio.sleep(0.4)
            engine.db.health('longbridge_news','ok',f'checked {len(symbols)} symbols')
        except asyncio.CancelledError:raise
        except Exception as exc:
            ctx=None
            engine.db.health('longbridge_news','error',str(exc))
            log.warning('Longbridge news error %s',exc)
        await asyncio.sleep(settings.lb_news_interval)
