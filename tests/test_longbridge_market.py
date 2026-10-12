import asyncio
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from app.instruments import resolve_instrument
from app.market import MarketError


def test_longbridge_history_sessions_and_limits(monkeypatch):
    from app.longbridge_market import longbridge_candles
    calls = []
    class Context:
        def __init__(self, config): pass
        def candlesticks(self, *args):
            calls.append(args)
            return [NS(timestamp=datetime(2026, 1, 6, tzinfo=timezone.utc), open=10, high=12,
                       low=9, close=11, volume=10, trade_session='Overnight')]
    sdk = NS(QuoteContext=Context, Period=NS(Min_5=5, Min_60=60, Day=1440, Week=10080),
             AdjustType=NS(NoAdjust=0), TradeSessions=NS(All=4, Intraday=1))
    monkeypatch.setitem(sys.modules, 'longbridge.openapi', sdk)
    monkeypatch.setattr('app.longbridge_market.lb_config', lambda settings=None: None)

    settings = NS(lb_overnight_enabled=True)
    for interval, period in [('5m', 5), ('1h', 60)]:
        result = asyncio.run(longbridge_candles(settings, resolve_instrument('NVDA'), interval, 'overnight'))
        assert calls[-1] == ('NVDA.US', period, 1000, 0, 4)
        assert {b['session'] for b in result['bars']} == {'overnight'}
    for interval, period in [('1d', 1440), ('1wk', 10080)]:
        asyncio.run(longbridge_candles(settings, resolve_instrument('NVDA'), interval, 'regular'))
        assert calls[-1] == ('NVDA.US', period, 1000, 0, 1)
    settings.lb_overnight_enabled = False
    with pytest.raises(MarketError, match='夜盘'):
        asyncio.run(longbridge_candles(settings, resolve_instrument('NVDA'), '5m', 'overnight'))
    with pytest.raises(MarketError, match='映射'):
        asyncio.run(longbridge_candles(settings, resolve_instrument('005930.KS'), '1d', 'regular'))
    Context.candlesticks = lambda *args: (_ for _ in ()).throw(RuntimeError('SECRET permission denied'))
    with pytest.raises(MarketError) as error:
        asyncio.run(longbridge_candles(settings, resolve_instrument('NVDA'), '1d', 'regular'))
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('start,end', [('2026-10-09T19:30:00+00:00','2026-10-09T20:00:00+00:00'),
                                    ('2026-11-27T17:30:00+00:00','2026-11-27T18:00:00+00:00')])
def test_longbridge_unknown_session_end_is_not_an_asserted_close(start, end):
    from app.longbridge_market import decode_candlesticks
    row=NS(timestamp=datetime.fromisoformat(start),open=10,high=12,low=9,close=11,volume=1,trade_session='Intraday')
    now=datetime.fromisoformat(end).timestamp()+300
    bar=decode_candlesticks([row],resolve_instrument('NVDA'),'1h',now)[0]
    assert bar['closed_at'] is None
    assert bar['complete'] is False  # Conservative until the nominal interval expires.
    assert decode_candlesticks([row],resolve_instrument('NVDA'),'1h',now+3600)[0]['complete'] is True
