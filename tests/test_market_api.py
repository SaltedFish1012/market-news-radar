import asyncio
import time
import pytest
from app.market import MarketData, MarketError
from app.market_sessions import Quote
from test_research import web_client


def test_backtest_market_guards(web_client, monkeypatch):
    web_client, token = web_client
    web_client.post('/api/session', json={'token':token})
    import app.main as main
    async def forbidden(*args, **kwargs):
        raise AssertionError('guard must precede upstream request')
    monkeypatch.setattr(main.market, 'candles', forbidden)
    for params in [{'symbol':'7203.T'}, {'symbol':'^N225'}, {'symbol':'NVDA','session':'overnight'}]:
        response = web_client.get('/api/strategies/backtest', params=params)
        assert response.status_code == 422
    assert web_client.get('/api/market/candles?session=invalid').status_code == 422
    assert 'instruments' in web_client.get('/api/watchlist').json()
    assert 'sessions' in web_client.get('/api/quotes').json()


def test_quote_reference_precedes_quote(web_client, monkeypatch):
    import app.main as main
    now = time.time()
    main.engine.db.put_session_quote(Quote('NVDA',110,'USD','Longbridge','pre',now,now,'normal'))
    async def history(*args, **kwargs):
        return {'bars':[{'session':'regular','complete':True,'closed_at':now-100,'close':100},
                        {'session':'regular','complete':True,'closed_at':now+100,'close':200}],
                'name':'NVIDIA','price':90,'quote_time':now,'source':'Yahoo','is_demo':False}
    monkeypatch.setattr(main.market, 'candles', history)
    item = asyncio.run(main.market.overview(['NVDA'],session='pre'))[0]
    assert item['display_quote']['price'] == 110
    assert item['reference_close']['closed_at'] < item['display_quote']['quote_at']
    assert item['change_pct'] == pytest.approx(10)
    async def unavailable(*args, **kwargs): raise MarketError('missing')
    monkeypatch.setattr(main.market, 'candles', unavailable)
    item = asyncio.run(main.market.overview(['NVDA'],session='post'))[0]
    assert item['display_quote'] is None and item['session_quotes'][0]['price'] == 110
    assert item['reference_close'] is None


def test_inflight_configuration_cannot_fill_new_cache(web_client, monkeypatch):
    import app.main as main
    market = MarketData(main.settings)
    async def fetch(*args):
        from dataclasses import replace
        market.settings = replace(main.settings, lb_overnight_enabled=False)
        return market._demo('NVDA','1d')
    monkeypatch.setattr(market, '_yahoo', fetch)
    result = asyncio.run(market.candles('NVDA'))
    assert result['session'] == 'regular' and result['instrument']['currency'] == 'USD'
    assert not market.cache
