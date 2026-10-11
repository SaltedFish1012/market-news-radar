import math
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.market import MarketData, MarketError, normalize_bars
from app.strategies import CATALOG, backtest, ema, indicators, rsi, strategy_snapshot, targets


def make_bars(count=160):
    rows = []
    for i in range(count):
        price = 100 + 8*math.sin(i/9) + i*.1
        rows.append({'time': 1700000000+i*86400, 'open': price-1, 'high': price+2,
                     'low': price-2, 'close': price, 'volume': 1000+i, 'complete': True})
    return rows


def test_indicator_reference_values():
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert rsi([100]*30)[-1] == 50
    assert rsi(list(range(1, 40)))[-1] == 100
    assert rsi(list(range(40, 1, -1)))[-1] == 0
    ind = indicators(make_bars())
    assert all(len(v) == 160 for v in ind.values())
    assert all(ind['boll_lower'][i] <= ind['boll_mid'][i] <= ind['boll_upper'][i] for i in range(19, 160))


@pytest.mark.parametrize('strategy', [s['id'] for s in CATALOG])
def test_no_future_data_changes_past_signals_or_equity(strategy):
    bars = make_bars()
    prefix, full = bars[:120], bars
    assert targets(full, indicators(full), strategy)[:120] == targets(prefix, indicators(prefix), strategy)
    small, large = backtest(prefix, strategy), backtest(full, strategy)
    assert small['equity'] == large['equity'][:len(small['equity'])]
    assert all(e['time'] > e['signal_time'] for e in large['executions'])
    assert all(math.isfinite(r['equity']) and r['equity'] > 0 for r in large['equity'])


def test_fills_use_next_open_and_costs_reduce_same_trade_equity():
    bars = make_bars()
    zero = backtest(bars, 'ema_cross', fee_bps=0, slippage_bps=0)
    cost = backtest(bars, 'ema_cross', fee_bps=10, slippage_bps=10)
    assert cost['final_equity'] < zero['final_equity']
    indexed = {b['time']: b for b in bars}
    for fill in zero['executions']:
        assert fill['price'] == indexed[fill['time']]['open']
    assert zero['benchmark_return_pct'] == pytest.approx((bars[-1]['close']/bars[60]['open']-1)*100, abs=1e-5)


def test_incomplete_bar_cannot_change_signal_or_backtest():
    bars = make_bars()
    extra = {**bars[-1], 'time': bars[-1]['time']+86400, 'close': 10000, 'high': 10001, 'complete': False}
    assert backtest(bars, 'ema_cross') == backtest(bars+[extra], 'ema_cross')
    assert strategy_snapshot(bars) == strategy_snapshot(bars+[extra])


def test_unknown_strategy_and_insufficient_history():
    with pytest.raises(ValueError):
        backtest(make_bars(30), 'ema_cross')
    with pytest.raises(ValueError):
        backtest(make_bars(), 'future_magic')
    assert strategy_snapshot(make_bars(20))['items'][0]['state'] == 'insufficient'


def test_bad_provider_rows_are_dropped_sorted_and_deduplicated():
    good = {'time': 1700000000, 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 1000}
    rows = normalize_bars([good, {**good, 'time': 1699000000}, good,
                           {**good, 'close': float('nan')}, {**good, 'high': 1},
                           {**good, 'low': None}, {**good, 'volume': -2}], '1d')
    assert [r['time'] for r in rows] == [1699000000, 1700000000]


def test_provider_failure_never_becomes_demo(monkeypatch):
    import asyncio
    client = MarketData(SimpleNamespace(alpaca_key='', alpaca_secret=''))
    async def fail(*args):
        raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(client, '_yahoo', fail)
    with pytest.raises(MarketError):
        asyncio.run(client.candles('NVDA'))
    with pytest.raises(MarketError, match='Alpaca'):
        asyncio.run(client.candles('NVDA', provider='alpaca'))
    demo = asyncio.run(client.candles('NVDA', provider='demo'))
    assert demo['is_demo'] and demo['provider'] == 'demo' and len(demo['bars']) == 260
    demo['bars'].clear()
    assert len(asyncio.run(client.candles('NVDA', provider='demo'))['bars']) == 260


@pytest.fixture
def web_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.config import Settings
    import json
    config = tmp_path / 'config'
    config.mkdir()
    (config/'watchlist.json').write_text(json.dumps({'symbols':['NVDA'], 'price_rules':[]}))
    (config/'feeds.json').write_text('[]')
    settings = Settings(token='local-test-token-with-32-characters', db_path=str(tmp_path/'test.db'),
                        config_dir=config, alpaca_key='', alpaca_secret='', lb_enabled=False,
                        lb_news_enabled=False, ai_key='', bark_key='', telegram_token='')
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'market', MarketData(settings))
    with TestClient(main.app) as client:
        yield client, settings.token
    main.engine.db.conn.close()


def test_dashboard_session_and_research_endpoints(web_client):
    client, token = web_client
    assert client.get('/').status_code == 200
    for asset in ('dashboard.js', 'dashboard.css'):
        assert client.get('/assets/'+asset).status_code == 200
    assert client.get('/api/market/candles?provider=demo').status_code == 401
    assert client.post('/api/session', json={'token':'错误口令'}).status_code == 403
    response = client.post('/api/session', json={'token':token})
    assert response.status_code == 200 and 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert token not in response.headers['set-cookie']
    assert client.get('/api/watchlist').json()['symbols'] == ['NVDA']
    data = client.get('/api/market/candles?provider=demo').json()
    assert data['is_demo'] and len(data['strategies']['items']) == 5
    for strategy in CATALOG:
        response = client.get('/api/strategies/backtest', params={'provider':'demo','strategy':strategy['id']})
        assert response.status_code == 200 and response.json()['is_demo']
    assert client.get('/api/market/candles?symbol=../../../etc/passwd').status_code == 422
    assert client.get('/api/market/candles?provider=unknown').status_code == 422
    assert client.get('/api/strategies/backtest?provider=demo&strategy=unknown').status_code == 422
    assert client.get('/api/strategies/backtest?provider=demo&fee_bps=-1').status_code == 422
    assert client.get('/api/strategies/backtest?provider=demo&initial_cash=nan').status_code == 422
    assert client.delete('/api/session').status_code == 200
    assert client.get('/api/status').status_code == 401


def test_forged_and_expired_sessions_rejected(web_client):
    client, token = web_client
    import hashlib, hmac
    payload = str(int(time.time())-5)+'.expired'
    signature = hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()
    client.cookies.set('radar_session', payload+'.'+signature)
    assert client.get('/api/status').status_code == 401
    client.cookies.set('radar_session', str(int(time.time())+3600)+'.fake.bad-signature')
    assert client.get('/api/status').status_code == 401
