from __future__ import annotations
import asyncio, hashlib, hmac, logging, os, re, secrets, time
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Query, Cookie, Depends, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, StrictBool
from fastapi.responses import HTMLResponse

from .config import settings
from .settings_store import SettingsStore, SettingsUpdate, ConfigConflict
from pydantic import ValidationError
from .ai_client import request_analysis, AIError
from .market import MarketData, MarketError, INTERVALS
from .strategies import CATALOG, indicators, strategy_snapshot, backtest
from .signals import norm_symbol, PriceRules
from .instruments import resolve_instrument
from .market_sessions import Quote, quote_view
from .engine import Engine, notification_loop
from .feeds import rss_loop
from .connectors import alpaca_news_loop,longbridge_quote_loop,longbridge_news_loop
from .x_news import x_news_loop, XClient, XAPIError
from .x_views import XPreviewRequest, preview_posts

logging.basicConfig(level='INFO',format='%(asctime)s %(levelname)s %(name)s: %(message)s')
# HTTP request logs can contain Telegram/Bark credentials in paths.
logging.getLogger('httpx').setLevel(logging.WARNING)
engine:Engine|None=None
market=MarketData(settings)
ASSETS=Path(__file__).parent / "assets"
config_store: SettingsStore | None = None
settings_lock = asyncio.Lock()
jobs = {}
JOB_FUNCTIONS = {'rss': rss_loop, 'alpaca_news': alpaca_news_loop,
                 'x_news':x_news_loop,
                 'longbridge_quote': longbridge_quote_loop, 'longbridge_news': longbridge_news_loop,
                 'notification': notification_loop}


def start_jobs(names=None):
    for name in (names if names is not None else JOB_FUNCTIONS):
        jobs[name] = asyncio.create_task(JOB_FUNCTIONS[name](engine, settings), name=name)


async def stop_jobs(names=None):
    selected = [jobs.pop(name) for name in list(names if names is not None else jobs) if name in jobs]
    for task in selected:
        task.cancel()
    if selected:
        await asyncio.gather(*selected, return_exceptions=True)


async def stop_ai_tasks():
    pending = list(engine.tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def sync_longbridge_env(config):
    mapping = {'LONGBRIDGE_APP_KEY': config.lb_app_key, 'LONGBRIDGE_APP_SECRET': config.lb_app_secret,
               'LONGBRIDGE_ACCESS_TOKEN': config.lb_access_token, 'LONGBRIDGE_CLIENT_ID': config.lb_client_id,
               'LONGBRIDGE_ENABLE_OVERNIGHT': str(config.lb_overnight_enabled).lower()}
    for name, value in mapping.items():
        os.environ[name] = value


async def apply_runtime(config):
    global settings
    old = settings
    groups = {
        'x_news':('x_enabled','x_bearer_token','x_poll_interval','x_accounts'),
        'rss': ('rss_interval', 'sec_contact', 'feeds_override'),
        'alpaca_news': ('alpaca_key', 'alpaca_secret', 'alpaca_url', 'alpaca_news_enabled'),
        'longbridge_quote': ('lb_enabled', 'lb_app_key', 'lb_app_secret', 'lb_access_token', 'lb_client_id','lb_overnight_enabled'),
        'longbridge_news': ('lb_news_enabled', 'lb_app_key', 'lb_app_secret', 'lb_access_token', 'lb_client_id', 'lb_news_interval'),
        'notification': ('notifications_enabled', 'telegram_token', 'telegram_chat', 'bark_key', 'bark_base'),
    }
    restart = {name for name, fields in groups.items() if any(getattr(old, f) != getattr(config, f) for f in fields)}
    new_watchlist = config.read_json('watchlist.json')
    if (new_watchlist['symbols'] != engine.watchlist['symbols'] or
            new_watchlist.get('instruments') != engine.watchlist.get('instruments')):
        restart.update(('longbridge_quote', 'longbridge_news'))
    await stop_jobs(restart)
    ai_changed = any(getattr(old,f) != getattr(config,f) for f in ('ai_enabled','ai_key','ai_provider','ai_model','ai_base_url'))
    if ai_changed:
        await stop_ai_tasks()
    settings = config
    engine.settings = config
    if engine.watchlist.get('price_rules') != new_watchlist.get('price_rules'):
        engine.rules = PriceRules(new_watchlist)
    engine.watchlist = new_watchlist
    market.settings = config
    if old != config or new_watchlist != engine.watchlist:
        market.cache.clear()
    sync_longbridge_env(config)
    if ai_changed:
        engine.db.health('ai', 'ready' if config.ai_enabled else 'disabled',
                         f'{config.ai_provider} / {config.ai_model}; configured, not yet verified' if config.ai_enabled else 'AI analysis paused')
    old_feeds = {f['name'] for f in old.read_json('feeds.json')}
    active_feeds = {f['name'] for f in config.read_json('feeds.json') if f.get('enabled')}
    for name in old_feeds - active_feeds:
        engine.db.health(name, 'disabled', 'Feed disabled or removed in settings')
    start_jobs(restart)



@asynccontextmanager
async def lifespan(app:FastAPI):
    global engine, settings, config_store, settings_lock
    settings.validate()
    config_store = SettingsStore(settings)
    settings = config_store.settings()
    settings_lock = asyncio.Lock()
    market.settings = settings
    sync_longbridge_env(settings)
    engine = Engine(settings)
    market.db = engine.db
    market.cache.clear()
    engine.db.health('service', 'ok', 'running')
    engine.db.health('ai', 'ready' if settings.ai_enabled else 'disabled',
                     f'{settings.ai_provider} / {settings.ai_model}; configured, not yet verified' if settings.ai_enabled else 'AI analysis paused')
    start_jobs()
    try:
        yield
    finally:
        await stop_jobs()
        await stop_ai_tasks()
        engine.db.health('service','stopped','shutdown')
        engine.db.conn.close()

app=FastAPI(title='Market News Radar',description='Multi-market research and attributed news; never trades',version='0.4.0',lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Pydantic's default error includes request inputs, potentially API keys.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']} for error in exc.errors()
    ]})


app.mount('/assets', StaticFiles(directory=ASSETS), name='assets')


def guard(authorization: str | None, session: str | None = None):
    if authorization:
        if not authorization.startswith('Bearer '):
            raise HTTPException(401, 'Bearer token required')
        if not hmac.compare_digest(authorization[7:].encode(), settings.token.encode()):
            raise HTTPException(403, 'Incorrect token')
        return
    if session:
        try:
            expiry, nonce, signature = session.split('.')
            payload = expiry + '.' + nonce
            expected = hmac.new(settings.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if int(expiry) > time.time() and hmac.compare_digest(signature, expected):
                return
        except (ValueError, TypeError):
            pass
    raise HTTPException(401, '请使用 DASHBOARD_TOKEN 登录')


def auth(authorization: str | None = Header(default=None), radar_session: str | None = Cookie(default=None)):
    guard(authorization, radar_session)


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=512, repr=False)
    remember: StrictBool = False


SESSION_TTL_SECONDS = 12 * 3600
REMEMBER_SESSION_TTL_SECONDS = 30 * 24 * 3600


@app.post('/api/session')
async def login(body: Login, response: Response, request: Request):
    guard('Bearer ' + body.token)
    ttl = REMEMBER_SESSION_TTL_SECONDS if body.remember else SESSION_TTL_SECONDS
    expiry = int(time.time()) + ttl
    payload = str(expiry) + '.' + secrets.token_hex(16)
    signature = hmac.new(settings.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    response.set_cookie('radar_session', payload + '.' + signature, httponly=True, samesite='strict', path='/',
                        secure=request.url.scheme == 'https',
                        max_age=ttl if body.remember else None,
                        expires=ttl if body.remember else None)
    response.headers['Cache-Control'] = 'no-store'
    return {'authenticated': True, 'remember': body.remember, 'expires_at': expiry}


@app.delete('/api/session')
async def logout(response: Response):
    response.delete_cookie('radar_session', path='/')
    return {'authenticated': False}


@app.get('/health')
async def health():
    return {'status':'running'}

@app.get('/api/status')
async def status(_auth=Depends(auth)):
    accounts = []
    for account in settings.x_accounts:
        user = engine.db.x_user(account['username'])
        cursor = engine.db.x_cursor(user['id'] if user else 'username:'+account['username'].casefold())
        accounts.append({'configured_username':account['username'],'enabled':settings.x_enabled and account['enabled'],
                         'user':user,**{k:cursor.get(k) for k in ('initialized','last_success_at','last_post_at','retry_at')},
                         'error':config_store.redact(cursor.get('error',''))})
    return {'x_accounts':accounts,'source_health':[{**r, 'detail': config_store.redact(r['detail'])} for r in engine.db.list_rows('health')],'configured':{
        'x_news':bool(settings.x_enabled and settings.x_bearer_token),
        'alpaca_news':bool(settings.alpaca_news_enabled and settings.alpaca_key and settings.alpaca_secret),
        'longbridge_quote':settings.lb_enabled,'longbridge_news':settings.lb_news_enabled,
        'ai':bool(settings.ai_enabled and settings.ai_key),'notifications':bool(settings.notifications_enabled and (settings.bark_key or (settings.telegram_token and settings.telegram_chat)))}}

@app.get('/api/news')
async def news(limit:int=Query(30,ge=1,le=200),source_type:str=Query('all',pattern='^(all|news)$'),_auth=Depends(auth)):
    return {'items':engine.db.news_rows(limit=limit,source_type=source_type)}

@app.get('/api/x/posts')
async def x_posts(limit:int=Query(100,ge=1,le=100),
                  username:str|None=Query(None,pattern=r'^@?[A-Za-z0-9_]{1,15}$'),_auth=Depends(auth)):
    return {'items':engine.db.x_posts(limit=limit,username=username),'accounts':engine.db.x_accounts()}

@app.post('/api/x/preview')
async def x_preview(body:XPreviewRequest,_auth=Depends(auth)):
    if not settings.x_bearer_token:
        return {'state':'not_configured','items':[], 'detail':'请在设置中配置 X Bearer Token 后测试读取。'}
    client = XClient(settings.x_bearer_token)
    try:
        return await preview_posts(client,body.username)
    except XAPIError as error:
        details = {'authentication':'X Token 验证失败，请检查设置。','permission':'X 账号读取权限不足。',
                   'billing':'X 读取额度或计费不可用。','rate_limit':'X 请求限流，请在重试时间后读取。',
                   'not_found':'未找到这个 X 账号。','network':'X 网络请求失败。'}
        return {'state':'error','items':[],'kind':error.kind,'retry_at':error.retry_at,
                'detail':details.get(error.kind,'X 数据暂不可用，请稍后重试。')}
    finally:
        await client.close()

@app.get('/api/alerts')
async def alerts(limit:int=Query(40,ge=1,le=200),_auth=Depends(auth)):
    return {'items':[{**r, 'last_error': config_store.redact(r['last_error'])} for r in engine.db.list_rows('alerts',limit)]}

@app.get('/api/quotes')
async def quotes(_auth=Depends(auth)):
    return {'items':engine.db.list_rows('quotes',500), 'sessions':[quote_view(Quote(**q),time.time()) for q in engine.db.session_quotes()]}

@app.post('/api/send-test-alert')
async def test_alert(_auth=Depends(auth)):
    from datetime import datetime,timezone
    token=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    engine.db.add_alert('manual-test:'+token,'监控系统测试通知','这是人工触发的测试消息，不是市场事件。','info')
    return {'queued':True}


def checked_symbol(symbol):
    value = norm_symbol(symbol)
    if not value:
        raise HTTPException(422, '无效股票代码')
    return value


@app.get('/api/settings')
async def read_settings(response: Response, _auth=Depends(auth)):
    response.headers['Cache-Control'] = 'no-store'
    return config_store.snapshot()


@app.put('/api/settings')
async def save_settings(body: SettingsUpdate, response: Response, _auth=Depends(auth)):
    response.headers['Cache-Control'] = 'no-store'
    async with settings_lock:
        try:
            candidate = config_store.prepare(body)
            config = config_store.settings(candidate)
            config_store.commit(candidate)
        except ConfigConflict as exc:
            raise HTTPException(409, str(exc))
        except ValidationError as exc:
            raise HTTPException(422, '配置验证失败：' + '；'.join(e['msg'] for e in exc.errors()))
        except ValueError as exc:
            raise HTTPException(422, '配置验证失败：' + str(exc))
        except OSError:
            raise HTTPException(503, '保存失败：请检查本地数据目录是否可写；当前设置未修改')
        await apply_runtime(config)
        return {'saved': True, 'applied': True, 'settings': config_store.snapshot()}


@app.post('/api/settings/test-ai')
async def test_ai(body: SettingsUpdate, response: Response, _auth=Depends(auth)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        candidate = config_store.prepare(body)
        config = config_store.settings(candidate)
    except ConfigConflict as exc:
        raise HTTPException(409, str(exc))
    except ValidationError as exc:
        raise HTTPException(422, '配置验证失败：' + '；'.join(e['msg'] for e in exc.errors()))
    except ValueError as exc:
        raise HTTPException(422, '配置验证失败：' + str(exc))
    if not config.ai_key:
        raise HTTPException(422, '请填写或保存当前供应商的 API Key')
    if not engine.reserve_ai_request(config):
        raise HTTPException(429, '已达到本地每小时 AI 请求上限')
    event = {'source':'本地连接测试', 'title':'AI 模型连接测试，非真实市场事件',
             'summary':'这是一条合成测试消息。没有市场事实，返回 uncertain 和 unverified，关联股票为空。',
             'source_symbols':[], 'related_symbols':[], 'url':''}
    try:
        async with engine.ai_slots:
            result = await request_analysis(config, event)
    except AIError as exc:
        raise HTTPException(502, str(exc))
    return {'ok': True, 'provider': config.ai_provider, 'model': config.ai_model, 'analysis': result}


@app.get('/api/watchlist')
async def watchlist(_auth=Depends(auth)):
    return {'instruments':engine.watchlist.get('instruments',[]), 'symbols': engine.watchlist.get('symbols', []), 'strategies': CATALOG, 'market_provider': settings.market_provider}


@app.get('/api/market/overview')
async def overview(provider: str = Query('yahoo', pattern='^(yahoo|alpaca|longbridge|demo)$'),
                   session: str = Query('regular',pattern='^(pre|regular|post|overnight|all)$'), _auth=Depends(auth)):
    items = await market.overview(engine.watchlist.get('symbols', []), provider, session)
    success = sum(not item.get('error') for item in items)
    engine.db.health('market_' + provider, 'ok' if success else 'error',
                     f'{success}/{len(items)} watchlist symbols available' + ('; synthetic demonstration' if provider == 'demo' else ''))
    live = {q['symbol']: q for q in engine.db.list_rows('quotes', 500)}
    for item in items:
        quote = live.get(item['symbol'])
        if quote and provider != 'demo':
            item['longbridge_quote'] = quote
    return {'items': items, 'provider': provider}


@app.get('/api/market/candles')
async def candles(symbol: str = 'NVDA', interval: str = Query('1d', pattern='^(5m|1h|1d|1wk)$'),
                  provider: str = Query('yahoo', pattern='^(yahoo|alpaca|longbridge|demo)$'),
                  session: str = Query('regular',pattern='^(pre|regular|post|overnight|all)$'), _auth=Depends(auth)):
    try:
        data = await market.candles(checked_symbol(symbol), interval, provider, session)
        engine.db.health('market_' + provider, 'ok', f'{data["symbol"]}: {len(data["bars"])} {interval} bars; ' + data['source'])
        return {**data, 'indicators': indicators(data['bars']), 'strategies': strategy_snapshot(data['bars'])}
    except MarketError as exc:
        engine.db.health('market_' + provider, 'error', str(exc))
        raise HTTPException(502, str(exc))


@app.get('/api/strategies/backtest')
async def run_backtest(symbol: str = 'NVDA', interval: str = Query('1d', pattern='^(5m|1h|1d|1wk)$'),
                       provider: str = Query('yahoo', pattern='^(yahoo|alpaca|longbridge|demo)$'),
                       session: str = Query('regular',pattern='^(pre|regular|post|overnight|all)$'), strategy: str = 'ema_cross',
                       initial_cash: float = Query(10000, ge=100, le=100000000, allow_inf_nan=False),
                       fee_bps: float = Query(5, ge=0, le=100, allow_inf_nan=False),
                       slippage_bps: float = Query(5, ge=0, le=100, allow_inf_nan=False), _auth=Depends(auth)):
    try:
        instrument = market.instrument(checked_symbol(symbol))
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    if instrument.market != 'US' or instrument.asset_type == 'index' or session != 'regular':
        raise HTTPException(422, '成交回测仅支持美股股票／ETF 常规时段；其他市场可查看行情与指标')
    if strategy not in {s['id'] for s in CATALOG}:
        raise HTTPException(422, '未知策略')
    try:
        data = await market.candles(checked_symbol(symbol), interval, provider, session)
        result = backtest(data['bars'], strategy, initial_cash, fee_bps, slippage_bps)
        return {**result, 'symbol': data['symbol'], 'interval': interval, 'source': data['source'],
                'instrument':data['instrument'], 'currency':data['currency'], 'timezone':data['timezone'], 'session':session,
                'is_demo': data['is_demo'], 'data_notice': data['notice'], 'adjustment': data['adjustment']}
    except MarketError as exc:
        raise HTTPException(502, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get('/', response_class=HTMLResponse)
async def home():
    return render_dashboard()


WORKSPACE_PAGES = {
    'market': ('/', 'marketSection', '市场总览'),
    'strategies': ('/strategies', 'strategySection', '策略与回测'),
    'news': ('/news', 'newsPanel', '新闻中心'),
    'alerts': ('/alerts', 'alertsSection', '预警记录'),
    'sources': ('/sources', 'sourcesSection', '数据连接'),
    'settings': ('/settings', 'settingsSection', '设置'),
}


def render_dashboard(page='market'):
    _, section, label = WORKSPACE_PAGES[page]
    html = (ASSETS / 'index.html').read_text(encoding='utf-8')
    for filename in ('dashboard.css', 'navigation.js', 'dashboard.js', 'settings.js'):
        version = hashlib.sha256((ASSETS / filename).read_bytes()).hexdigest()[:12]
        html = html.replace(f'/assets/{filename}', f'/assets/{filename}?v={version}')
    html = re.sub(r'(<div id="[^"]+View" data-page="([^"]+)")(?: hidden)?(>)',
                  lambda match: match[1] + ('' if match[2] == page else ' hidden') + match[3], html)
    html = html.replace('class="nav-item active"', 'class="nav-item"')
    html = html.replace(f'class="nav-item" data-section="{section}"',
                        f'class="nav-item active" data-section="{section}" aria-current="page"')
    html = html.replace('Market Radar · 市场研究工作台', label + ' · Market Radar')
    if page == 'settings':
        html = html.replace('id="workspaceView"', 'id="workspaceView" hidden')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store'})


@app.get('/settings', response_class=HTMLResponse)
@app.get('/strategies', response_class=HTMLResponse)
@app.get('/news', response_class=HTMLResponse)
@app.get('/alerts', response_class=HTMLResponse)
@app.get('/sources', response_class=HTMLResponse)
async def workspace_page(request: Request):
    page = next(key for key, values in WORKSPACE_PAGES.items() if values[0] == request.url.path)
    return render_dashboard(page)
