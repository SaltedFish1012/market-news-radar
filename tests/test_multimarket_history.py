import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote
import httpx
import pytest
from app.ai_client import SYSTEM, Analysis, request_analysis
from app.instruments import resolve_instrument
from app.market import MarketData, MarketError
from app.strategies import indicators


@pytest.mark.parametrize('symbol',['005930.KS','035900.KQ','7203.T','^N225','600519.SS','000001.SZ'])
@pytest.mark.parametrize('interval',['5m','1h','1d','1wk'])
def test_other_market_history_metadata_and_indicators(monkeypatch,symbol,interval):
    instrument = resolve_instrument(symbol)
    actual_client = httpx.AsyncClient
    def respond(request):
        assert unquote(request.url.path.rsplit('/',1)[1]) == symbol
        rows = [1700000000,1700003600,1700100000]
        return httpx.Response(200,json={'chart':{'result':[{'meta':{'currency':instrument.currency,
            'tradingPeriods':{'regular':[[{'start':1700000000,'end':1700200000}]]}},
            'timestamp':rows,'indicators':{'quote':[{'open':[100]*3,'high':[101]*3,
            'low':[99]*3,'close':[100]*3,'volume':[1]*3}]}}]}})
    monkeypatch.setattr('app.market.httpx.AsyncClient',lambda **kw:actual_client(**kw,transport=httpx.MockTransport(respond)))
    result = asyncio.run(MarketData(SimpleNamespace()).candles(symbol,interval))
    assert result['symbol'] == symbol and result['currency'] == instrument.currency
    assert result['timezone'] == instrument.timezone and len(result['bars']) == 3
    assert len(indicators(result['bars'])['ema20']) == 3


def test_ai_multimarket_symbol_validation():
    assert '美股 ticker' not in SYSTEM and '内部代码' in SYSTEM
    valid = dict(sentiment='uncertain',affected_symbols=['005930.KS','^N225','000001.SZ'],
                 evidence_level='unverified',rationale='待确认',uncertainty='待核实')
    assert Analysis(**valid).affected_symbols == valid['affected_symbols']
    with pytest.raises(ValueError): Analysis(**{**valid,'affected_symbols':['../../secret']})
