"""Read-only OHLCV adapters. Public data is best effort, never a trading feed."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import random
import time
from dataclasses import asdict
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from .instruments import Instrument, resolve_instrument, SESSIONS
from .market_sessions import bar_is_complete, bar_closed_at, classify_bar_session, Quote, quote_view, reference_close

INTERVALS = {'5m': ('5d', 300), '1h': ('3mo', 3600), '1d': ('1y', 86400), '1wk': ('5y', 604800)}
NAMES = {'WDC': 'Western Digital', 'SNDK': 'SanDisk', 'MU': 'Micron Technology',
         'NVDA': 'NVIDIA', 'AVGO': 'Broadcom', 'AMD': 'AMD', 'SPY': 'S&P 500 ETF',
         'QQQ': 'Nasdaq 100 ETF', 'SOXX': 'Semiconductor ETF'}


class MarketError(Exception):
    pass


def is_complete(timestamp: int, interval: str) -> bool:
    return bar_is_complete(timestamp,interval,resolve_instrument('NVDA'),None,time.time())


def normalize_bars(rows: list[dict], interval: str, *, instrument: Instrument | None = None,
                   periods: dict | None = None, now: float | None = None) -> list[dict]:
    instrument = instrument or resolve_instrument('NVDA')
    now = time.time() if now is None else now
    out = {}
    for row in rows:
        try:
            t = int(row['time'])
            datetime.fromtimestamp(t,ZoneInfo(instrument.timezone))
            o, h, l, c = (float(row[k]) for k in ('open', 'high', 'low', 'close'))
            volume = float(row.get('volume') or 0)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
            continue
        if not math.isfinite(volume) or volume < 0 or l > min(o, c) or h < max(o, c) or l > h:
            continue
        session = row.get('session') or ('regular' if interval in ('1d','1wk') else classify_bar_session(t,periods))
        session = session if session in SESSIONS else 'unknown'
        closed = row.get('closed_at', bar_closed_at(t,interval,instrument,periods))
        closed = closed if isinstance(closed,(int,float)) and math.isfinite(closed) else None
        complete = closed <= now if closed is not None else bar_is_complete(t,interval,instrument,periods,now)
        out[t] = {'time': t, 'open': o, 'high': h, 'low': l, 'close': c,
                  'volume': volume, 'complete': complete, 'closed_at':closed,'session':session}
    return [out[t] for t in sorted(out)]


class MarketData:
    def __init__(self, settings):
        self.settings = settings
        self.cache = {}
        self.db = None
        self.slots = asyncio.Semaphore(4)

    def instrument(self, symbol: str) -> Instrument:
        configured = self.settings.read_json('watchlist.json').get('instruments', []) if hasattr(self.settings,'read_json') else []
        return resolve_instrument(symbol,configured)

    async def candles(self, symbol: str, interval: str = '1d', provider: str = 'yahoo', session: str = 'regular') -> dict:
        try:
            instrument = self.instrument(symbol)
        except ValueError as exc:
            raise MarketError(str(exc)) from exc
        symbol = instrument.symbol
        if interval not in INTERVALS or session not in (*SESSIONS,'all'):
            raise MarketError('未知周期或时段')
        if interval in ('1d','1wk') and session not in ('regular','all'):
            raise MarketError('延长时段 K 线请选择分钟周期')
        if provider == 'yahoo' and session == 'overnight':
            raise MarketError('Yahoo 不支持可信夜盘数据，请选择长桥')
        if provider == 'alpaca' and (session != 'regular' or instrument.market != 'US'):
            raise MarketError('Alpaca IEX 本轮仅支持美股常规时段，请选择长桥或公共历史源')
        if provider == 'demo' and session != 'regular':
            raise MarketError('合成演示仅提供常规时段')
        request_settings = self.settings
        key = (symbol, provider, interval, session, id(request_settings), repr(instrument))
        cached = self.cache.get(key)
        ttl = 60 if interval in ('5m', '1h') else 300
        if cached and time.monotonic() - cached[0] < ttl:
            result = copy.deepcopy(cached[1])
            result['cached'] = True
            return result
        async with self.slots:
            try:
                if provider == 'demo':
                    result = self._demo(symbol, interval)
                elif provider == 'alpaca':
                    result = await self._alpaca(symbol, interval)
                elif provider == 'longbridge':
                    from .longbridge_market import longbridge_candles
                    result = await longbridge_candles(request_settings, instrument, interval, session)
                else:
                    result = await self._yahoo(symbol, interval, session)
            except MarketError:
                raise
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                # Provider exceptions can contain URLs; do not expose credential-bearing requests.
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                raise MarketError(f'行情源暂不可用（HTTP {status}）' if status else '行情请求失败，请稍后重试或切换数据源') from exc
        if not result['bars']:
            raise MarketError('该股票在所选周期没有可用 K 线')
        if provider == 'demo':
            result['currency'] = instrument.currency
        supported = ['regular','pre','post','all'] if provider == 'yahoo' and interval in ('5m','1h') and instrument.market == 'US' else (list(SESSIONS) + ['all'] if provider == 'longbridge' and interval in ('5m','1h') else ['regular'])
        if provider == 'longbridge' and not request_settings.lb_overnight_enabled:
            supported = [s for s in supported if s != 'overnight']
        result.update(symbol=symbol, interval=interval, instrument=asdict(instrument), timezone=instrument.timezone,
                      session=session, coverage={'supported_sessions':supported,
                      'actual_sessions':sorted({b['session'] for b in result['bars']}),'notice':result['notice']},
                      fetched_at=datetime.now(timezone.utc).isoformat(), cached=False)
        if self.settings is request_settings:
            self.cache[key] = (time.monotonic(), copy.deepcopy(result))
        if len(self.cache) > 256:
            self.cache.pop(next(iter(self.cache)))
        return result

    async def _yahoo(self, symbol, interval, session='regular'):
        instrument = self.instrument(symbol)
        provider_symbol = instrument.provider_symbols['yahoo']
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(provider_symbol,safe="")}',
                params={'interval': interval, 'range': INTERVALS[interval][0], 'includePrePost': 'true' if session in ('pre','post','all') else 'false'},
                headers={'User-Agent': 'Mozilla/5.0 MarketNewsRadar/0.2'})
            response.raise_for_status()
        chart = response.json().get('chart', {})
        if chart.get('error') or not chart.get('result'):
            raise MarketError('公共行情源未返回该股票数据')
        data = chart['result'][0]
        meta = data['meta']
        if meta.get('currency') and meta['currency'] != instrument.currency:
            raise MarketError('供应商币种与标的配置不一致，请核查代码映射')
        periods = {'tradingPeriods':meta.get('tradingPeriods',data.get('tradingPeriods')),
                   'currentTradingPeriod':meta.get('currentTradingPeriod')}
        quotes = data['indicators']['quote'][0]
        adj = data['indicators'].get('adjclose', [{}])[0].get('adjclose', [])
        rows = []
        for i, timestamp in enumerate(data.get('timestamp') or []):
            raw = {k: quotes.get(k, [])[i] if i < len(quotes.get(k, [])) else None
                   for k in ('open', 'high', 'low', 'close', 'volume')}
            factor = adj[i] / raw['close'] if i < len(adj) and adj[i] and raw['close'] else 1
            rows.append({'time': timestamp, **{k: (raw[k] * factor if raw[k] is not None else None)
                         for k in ('open', 'high', 'low', 'close')}, 'volume': raw['volume']})
        bars = normalize_bars(rows, interval, instrument=instrument, periods=periods)
        if interval in ('5m','1h') and session != 'all':
            bars = [b for b in bars if b['session'] == session]
        return {'bars': bars, 'provider': 'yahoo' , 'source': 'Yahoo Finance · 公共行情',
                'name': instrument.name if instrument.name != symbol else meta.get('longName') or meta.get('shortName') or NAMES.get(symbol, symbol),
                'currency': instrument.currency, 'exchange': meta.get('exchangeName', ''),
                'price': meta.get('regularMarketPrice') if session == 'regular' else (bars[-1]['close'] if bars else None),
                'quote_time': meta.get('regularMarketTime') if session == 'regular' else (bars[-1]['time'] if bars else None),
                'previous_close': meta.get('chartPreviousClose') if interval == '5m' else None,
                'adjustment': '日/周线采用供应商复权价格；分钟线按原始 OHLC',
                'is_demo': False, 'notice': '公共接口可能延迟或中断；分钟历史区间有限。时段和收盘依据供应商元数据，缺失时保守标为未知或未收盘。'}

    async def _alpaca(self, symbol, interval):
        if not self.settings.alpaca_key or not self.settings.alpaca_secret:
            raise MarketError('请先在 .env 配置 Alpaca API Key 和 Secret')
        days = {'5m': 7, '1h': 90, '1d': 370, '1wk': 1850}[interval]
        end = datetime.now(timezone.utc) - timedelta(minutes=16)
        rows, page = [], None
        async with httpx.AsyncClient(timeout=18) as client:
            for _ in range(5):
                params = {'timeframe': {'5m': '5Min', '1h': '1Hour', '1d': '1Day', '1wk': '1Week'}[interval],
                          'start': (end - timedelta(days=days)).isoformat(), 'end': end.isoformat(),
                          'adjustment': 'all', 'feed': 'iex', 'limit': 10000, 'sort': 'asc'}
                if page:
                    params['page_token'] = page
                response = await client.get(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars', params=params,
                    headers={'APCA-API-KEY-ID': self.settings.alpaca_key, 'APCA-API-SECRET-KEY': self.settings.alpaca_secret})
                response.raise_for_status()
                data = response.json()
                rows.extend({'time': int(datetime.fromisoformat(b['t'].replace('Z', '+00:00')).timestamp()),
                             'open': b['o'], 'high': b['h'], 'low': b['l'], 'close': b['c'], 'volume': b['v']}
                            for b in (data.get('bars') or []))
                page = data.get('next_page_token')
                if not page:
                    break
        bars = normalize_bars([{**row,'session':'regular'} for row in rows], interval, instrument=self.instrument(symbol))
        return {'bars': bars, 'provider': 'alpaca', 'source': 'Alpaca · IEX', 'is_demo': False,
                'name': NAMES.get(symbol, symbol), 'currency': 'USD', 'exchange': 'IEX',
                'price': bars[-1]['close'] if bars else None, 'quote_time': bars[-1]['time'] if bars else None,
                'adjustment': 'Alpaca all adjustment',
                'notice': 'IEX 单一交易所数据，非全市场成交量；查询结束时间保守滞后 16 分钟。'}

    def _demo(self, symbol, interval):
        seed = int(hashlib.sha256((symbol + interval).encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        step = INTERVALS[interval][1]
        end = int(time.time() // step * step) - step
        price = {'NVDA': 120, 'MU': 100, 'WDC': 80, 'SPY': 550, 'QQQ': 480}.get(symbol, 150)
        rows = []
        for i in range(260):
            o = price * (1 + rng.uniform(-.004, .004))
            price = o * (1 + .001 + .009 * math.sin(i / 15) + rng.gauss(0, .012))
            rows.append({'time': end - (259-i) * step, 'open': o, 'close': price,
                         'high': max(o, price) * (1 + rng.uniform(.001, .012)),
                         'low': min(o, price) * (1 - rng.uniform(.001, .012)), 'volume': rng.randint(100000, 12000000)})
        return {'bars': normalize_bars([{**r,'session':'regular'} for r in rows], interval, instrument=self.instrument(symbol)), 'provider': 'demo', 'source': '演示 · 合成数据',
                'is_demo': True, 'name': NAMES.get(symbol, symbol), 'currency': 'USD', 'exchange': 'DEMO',
                'price': price, 'quote_time': end, 'adjustment': '无',
                'notice': '这是一组合成 K 线，日期可能包含非交易日；信号和回测仅用于演示功能，不是市场结果。'}

    async def overview(self, symbols, provider='yahoo', session='regular'):
        async def fetch(symbol):
            instrument = self.instrument(symbol)
            quotes = [quote_view(Quote(**row), time.time()) for row in
                      (self.db.session_quotes(symbol) if self.db else []) if row['currency'] == instrument.currency]
            allowed = [q for q in quotes if q['session'] == session or (session == 'all' and q['session'] in SESSIONS)]
            display = max(allowed, key=lambda q:(q['fresh'],q['quote_at'] or 0), default=None)
            item = {'symbol':instrument.symbol, 'name':instrument.name, 'instrument':asdict(instrument),
                    'currency':instrument.currency, 'timezone':instrument.timezone, 'session':session,
                    'session_quotes':quotes, 'public_quote':None, 'display_quote':display,
                    'reference_close':None, 'change_pct':None, 'price':display['price'] if display else None,
                    'source':display['source'] if display else '', 'sparkline':[], 'is_demo':provider=='demo', 'error':None}
            try:
                data = await self.candles(symbol, '1d', provider, 'regular')
                public = {'symbol':symbol, 'price':data.get('price'), 'quote_at':data.get('quote_time'),
                          'currency':instrument.currency, 'source':data['source'], 'session':'regular',
                          'status':'合成演示' if data['is_demo'] else '公共／历史报价', 'fresh':False}
                item.update(name=data['name'],public_quote=public,sparkline=[b['close'] for b in data['bars'][-24:]])
                if session in ('regular','all') and not (display and display['fresh']):
                    item['display_quote'] = public if public['price'] is not None else display
                chosen = item['display_quote']
                if chosen and isinstance(chosen.get('quote_at'),(int,float)):
                    item['reference_close'] = reference_close(data['bars'],chosen['quote_at'])
            except MarketError as exc:
                item['error'] = str(exc)
            chosen, ref = item['display_quote'], item['reference_close']
            if chosen:
                item.update(price=chosen['price'],source=chosen['source'],quote_time=chosen['quote_at'])
                if ref and chosen['price'] is not None:
                    item['change_pct'] = (chosen['price']/ref['price']-1)*100
            return item
        return await asyncio.gather(*(fetch(s) for s in symbols[:20]))
