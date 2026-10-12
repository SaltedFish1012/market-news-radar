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
        assert pre['price'] == 99 and pre['quote_time'] == opening-300
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


@pytest.mark.parametrize('interval', ['5m', '1h'])
def test_alpaca_regular_history_filters_extensions_dst_and_early_close(monkeypatch, interval):
    calendars = [{'date':'2026-03-06','open':'09:30','close':'16:00'},
                 {'date':'2026-03-09','open':'09:30','close':'16:00'},
                 {'date':'2026-11-27','open':'09:30','close':'13:00'}]
    bars = [dict(t=t,o=p,h=p+2,l=p-1,c=p+1,v=v) for t,p,v in [
        ('2026-03-06T13:30:00Z',999,1), ('2026-03-06T14:25:00Z',888,1),
        ('2026-03-06T14:30:00Z',100,1), ('2026-03-06T15:25:00Z',110,2),
        ('2026-03-06T21:30:00Z',777,1), ('2026-03-09T13:30:00Z',200,3),
        ('2026-11-27T17:30:00Z',300,4), ('2026-11-27T17:55:00Z',310,5),
        ('2026-11-27T18:00:00Z',666,1)]]
    requests=[];real_client=httpx.AsyncClient
    def respond(request):
        requests.append(request)
        if request.url.path == '/v2/calendar': return httpx.Response(200,json=calendars)
        return httpx.Response(200,json={'bars':bars})
    monkeypatch.setattr('app.market.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.setattr('app.market.time.time',lambda:stamp('2026-11-28T00:00:00+00:00'))
    result=asyncio.run(MarketData(SimpleNamespace(alpaca_key='key',alpaca_secret='secret')).candles('NVDA',interval,'alpaca'))
    if interval == '5m':
        assert [b['close'] for b in result['bars']] == [101,111,201,301,311]
    else:
        assert [b['close'] for b in result['bars']] == [111,201,311]
        assert result['bars'][0]['open'] == 100 and result['bars'][0]['high'] == 112
        assert [b['volume'] for b in result['bars']] == [3,3,9]
        assert result['bars'][-1]['closed_at'] == stamp('2026-11-27T18:00:00+00:00')
        assert result['bars'][-1]['complete'] is True
    assert {b['session'] for b in result['bars']} == {'regular'}
    assert next(r for r in requests if r.url.path.endswith('/bars')).url.params['timeframe'] == '5Min'
    assert any(r.url.path == '/v2/calendar' for r in requests)


@pytest.mark.parametrize('calendar', [None, {}, [{'date':'2026-03-06','open':'bad','close':'16:00'}]])
def test_alpaca_missing_calendar_never_assumes_regular(monkeypatch, calendar):
    real_client=httpx.AsyncClient
    def respond(request):
        if request.url.path == '/v2/calendar': return httpx.Response(503) if calendar is None else httpx.Response(200,json=calendar)
        return httpx.Response(200,json={'bars':[{'t':'2026-03-06T14:30:00Z','o':100,'h':101,'l':99,'c':100,'v':1}]})
    monkeypatch.setattr('app.market.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(respond),**kw))
    with pytest.raises(MarketError,match='日历'):
        asyncio.run(MarketData(SimpleNamespace(alpaca_key='key',alpaca_secret='secret')).candles('NVDA','5m','alpaca'))
