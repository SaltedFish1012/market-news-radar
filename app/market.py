"""Read-only OHLCV adapters. Public data is best effort, never a trading feed."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

INTERVALS = {'5m': ('5d', 300), '1h': ('3mo', 3600), '1d': ('1y', 86400), '1wk': ('5y', 604800)}
NAMES = {'WDC': 'Western Digital', 'SNDK': 'SanDisk', 'MU': 'Micron Technology',
         'NVDA': 'NVIDIA', 'AVGO': 'Broadcom', 'AMD': 'AMD', 'SPY': 'S&P 500 ETF',
         'QQQ': 'Nasdaq 100 ETF', 'SOXX': 'Semiconductor ETF'}


class MarketError(Exception):
    pass


def is_complete(timestamp: int, interval: str) -> bool:
    now = datetime.now(ZoneInfo('America/New_York'))
    date = datetime.fromtimestamp(timestamp, ZoneInfo('America/New_York'))
    if interval == '1d':
        return date.date() < now.date() or (date.date() == now.date() and now.hour >= 16)
    if interval == '1wk':
        return date.isocalendar()[:2] < now.isocalendar()[:2]
    return timestamp + INTERVALS[interval][1] <= time.time()


def normalize_bars(rows: list[dict], interval: str) -> list[dict]:
    out = {}
    for row in rows:
        try:
            t = int(row['time'])
            o, h, l, c = (float(row[k]) for k in ('open', 'high', 'low', 'close'))
            volume = float(row.get('volume') or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
            continue
        if not math.isfinite(volume) or volume < 0 or l > min(o, c) or h < max(o, c) or l > h:
            continue
        out[t] = {'time': t, 'open': o, 'high': h, 'low': l, 'close': c,
                  'volume': volume, 'complete': is_complete(t, interval)}
    return [out[t] for t in sorted(out)]


class MarketData:
    def __init__(self, settings):
        self.settings = settings
        self.cache = {}
        self.slots = asyncio.Semaphore(4)

    async def candles(self, symbol: str, interval: str = '1d', provider: str = 'yahoo') -> dict:
        key = (symbol, interval, provider)
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
                else:
                    result = await self._yahoo(symbol, interval)
            except MarketError:
                raise
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                # Provider exceptions can contain URLs; do not expose credential-bearing requests.
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                raise MarketError(f'行情源暂不可用（HTTP {status}）' if status else '行情请求失败，请稍后重试或切换数据源') from exc
        if not result['bars']:
            raise MarketError('该股票在所选周期没有可用 K 线')
        result.update(symbol=symbol, interval=interval, fetched_at=datetime.now(timezone.utc).isoformat(), cached=False)
        self.cache[key] = (time.monotonic(), copy.deepcopy(result))
        if len(self.cache) > 256:
            self.cache.pop(next(iter(self.cache)))
        return result

    async def _yahoo(self, symbol, interval):
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}',
                params={'interval': interval, 'range': INTERVALS[interval][0], 'includePrePost': 'false'},
                headers={'User-Agent': 'Mozilla/5.0 MarketNewsRadar/0.2'})
            response.raise_for_status()
        chart = response.json().get('chart', {})
        if chart.get('error') or not chart.get('result'):
            raise MarketError('公共行情源未返回该股票数据')
        data = chart['result'][0]
        meta = data['meta']
        quotes = data['indicators']['quote'][0]
        adj = data['indicators'].get('adjclose', [{}])[0].get('adjclose', [])
        rows = []
        for i, timestamp in enumerate(data.get('timestamp') or []):
            raw = {k: quotes.get(k, [])[i] if i < len(quotes.get(k, [])) else None
                   for k in ('open', 'high', 'low', 'close', 'volume')}
            factor = adj[i] / raw['close'] if i < len(adj) and adj[i] and raw['close'] else 1
            rows.append({'time': timestamp, **{k: (raw[k] * factor if raw[k] is not None else None)
                         for k in ('open', 'high', 'low', 'close')}, 'volume': raw['volume']})
        return {'bars': normalize_bars(rows, interval), 'provider': 'yahoo', 'source': 'Yahoo Finance · 公共行情',
                'name': meta.get('longName') or meta.get('shortName') or NAMES.get(symbol, symbol),
                'currency': meta.get('currency', 'USD'), 'exchange': meta.get('exchangeName', ''),
                'price': meta.get('regularMarketPrice'), 'quote_time': meta.get('regularMarketTime'),
                'previous_close': meta.get('chartPreviousClose') if interval == '5m' else None,
                'adjustment': '日/周线采用供应商复权价格；分钟线按原始 OHLC',
                'is_demo': False, 'notice': '公共接口可能延迟或中断；K 线末根可能尚未收盘。仅展示供应商返回的数据。'}

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
        bars = normalize_bars(rows, interval)
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
        return {'bars': normalize_bars(rows, interval), 'provider': 'demo', 'source': '演示 · 合成数据',
                'is_demo': True, 'name': NAMES.get(symbol, symbol), 'currency': 'USD', 'exchange': 'DEMO',
                'price': price, 'quote_time': end, 'adjustment': '无',
                'notice': '这是一组合成 K 线，日期可能包含非交易日；信号和回测仅用于演示功能，不是市场结果。'}

    async def overview(self, symbols, provider='yahoo'):
        async def fetch(symbol):
            try:
                data = await self.candles(symbol, '1d', provider)
                bars = data['bars']
                # Quote-relative change is computed against prior daily bar, never range-start close.
                last = bars[-1]
                previous = bars[-2]['close'] if len(bars) > 1 else None
                price = data.get('price') or last['close']
                return {'symbol': symbol, 'name': data['name'], 'price': price,
                        'change_pct': (price / previous - 1) * 100 if previous else None,
                        'quote_time': data.get('quote_time'), 'source': data['source'], 'is_demo': data['is_demo'],
                        'sparkline': [b['close'] for b in bars[-24:]], 'error': None}
            except MarketError as exc:
                return {'symbol': symbol, 'name': NAMES.get(symbol, symbol), 'error': str(exc), 'price': None}
        return await asyncio.gather(*(fetch(s) for s in symbols[:20]))
