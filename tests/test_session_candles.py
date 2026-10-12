import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import httpx
import pytest
import app.market_sessions as sessions
from app.instruments import resolve_instrument
from app.market import MarketData, MarketError, normalize_bars


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def test_dst_midnight_and_early_close():
    assert hasattr(sessions, 'bar_is_complete'), 'market close semantics missing'
    instrument = resolve_instrument('NVDA')
    for opening, end in [('2026-03-06T14:30:00+00:00','2026-03-06T21:00:00+00:00'),
                         ('2026-03-09T13:30:00+00:00','2026-03-09T20:00:00+00:00')]:
        periods = {'regular':[{'start':stamp(opening),'end':stamp(end)}]}
        assert sessions.classify_bar_session(stamp(opening),periods) == 'regular'
        assert sessions.classify_bar_session(stamp(end),periods) == 'unknown'
    day = stamp('2026-11-27T14:30:00+00:00')
    periods = {'regular':[{'start':day,'end':stamp('2026-11-27T18:00:00+00:00')}]}
    assert sessions.bar_is_complete(day,'1d',instrument,periods,stamp('2026-11-27T18:01:00+00:00'))
    assert not sessions.bar_is_complete(day,'1d',instrument,periods,stamp('2026-11-27T17:59:00+00:00'))
    midnight = stamp('2026-10-12T01:00:00+00:00')
    previous = stamp('2026-10-11T14:00:00+00:00')
    assert sessions.bar_is_complete(previous,'1d',resolve_instrument('7203.T'),None,midnight) is True
    assert sessions.bar_is_complete(previous,'1d',instrument,None,midnight) is False


def test_missing_periods_are_conservative():
    assert hasattr(sessions, 'bar_is_complete'), 'market close semantics missing'
    now = stamp('2026-10-12T22:00:00+00:00')
    rows = normalize_bars([{'time':now-300,'open':100,'high':101,'low':99,'close':100}], '1d',
                         instrument=resolve_instrument('NVDA'),now=now)
    assert rows[0]['complete'] is False and rows[0]['closed_at'] is None
    assert not sessions.bar_is_complete(now-300,'1wk',resolve_instrument('NVDA'),None,now)


def test_yahoo_session_request_and_cache(monkeypatch):
    opening = stamp('2026-10-09T13:30:00+00:00')
    payload = {'chart':{'error':None,'result':[{'meta':{'currency':'USD','regularMarketPrice':100,
        'regularMarketTime':opening+300, 'tradingPeriods':{'pre':[[{'start':opening-300,'end':opening}]],
        'regular':[[{'start':opening,'end':opening+23400}]]}},
        'timestamp':[opening-300,opening], 'indicators':{'quote':[{'open':[99,100],'high':[100,101],
        'low':[98,99],'close':[99,100],'volume':[1,2]}]}}]}}
    requests=[]
    real_client=httpx.AsyncClient
    def response(request):
        requests.append(request)
        return httpx.Response(200,json=payload)
    monkeypatch.setattr('app.market.httpx.AsyncClient',lambda **kw: real_client(transport=httpx.MockTransport(response),**kw))
    market=MarketData(SimpleNamespace(alpaca_key='',alpaca_secret=''))
    async def run():
        pre=await market.candles('NVDA','5m','yahoo','pre')
        regular=await market.candles('NVDA','5m','yahoo','regular')
        all_data=await market.candles('NVDA','5m','yahoo','all')
        assert pre['bars'][0]['close'] == 99 and len(pre['bars']) == 1
        assert regular['bars'][0]['close'] == 100 and len(regular['bars']) == 1
        assert len(all_data['bars']) == 2 and len(requests) == 3
        assert requests[0].url.params['includePrePost'] == 'true'
        with pytest.raises(MarketError,match='夜盘'):
            await market.candles('NVDA','5m','yahoo','overnight')
        with pytest.raises(MarketError,match='分钟'):
            await market.candles('NVDA','1d','yahoo','pre')
    asyncio.run(run())


def test_currency_mismatch_is_an_error(monkeypatch):
    real_client=httpx.AsyncClient
    response={'chart':{'result':[{'meta':{'currency':'JPY'}, 'timestamp':[], 'indicators':{'quote':[{}]}}]}}
    monkeypatch.setattr('app.market.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response)),**kw))
    with pytest.raises(MarketError,match='币种'):
        asyncio.run(MarketData(SimpleNamespace()).candles('NVDA'))
