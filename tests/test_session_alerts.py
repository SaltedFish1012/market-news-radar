import asyncio
from types import SimpleNamespace
from app.engine import Engine
from app.signals import PriceRules
from app.market_sessions import Quote


CFG = {'symbols':['NVDA'], 'price_rules':[
    {'symbol':'NVDA','kind':'price_below','value':100,'sessions':['regular','overnight'],'cooldown_s':300}]}


def test_session_windows_and_cooldowns():
    rules = PriceRules(CFG)
    assert rules.feed('NVDA',101,1000,session='regular') == []
    assert rules.feed('NVDA',99,1001,session='overnight') == []
    assert len(rules.feed('NVDA',99,1002,session='regular')) == 1
    assert rules.feed('NVDA',101,1400,session='regular') == []
    assert rules.feed('NVDA',101,1003,session='overnight') == []
    assert len(rules.feed('NVDA',99,1004,session='overnight')) == 1
    assert rules.feed('NVDA',101,1401,session='pre') == []
    assert rules.feed('NVDA',99,1402,session='pre') == []


def test_five_minute_window_resets_across_session_and_gap():
    rules = PriceRules({'price_rules':[{'symbol':'NVDA','kind':'change_5m_below_pct','value':-2}]})
    assert rules.feed('NVDA',100,1000,session='regular') == []
    assert rules.feed('NVDA',90,1300,session='overnight') == []
    assert len(rules.feed('NVDA',97,1300,session='regular')) == 1
    assert rules.feed('NVDA',80,1800,session='regular') == []


def test_quotes_filter_stale_duplicate_status_and_currency(tmp_path):
    engine = Engine(SimpleNamespace(db_path=str(tmp_path/'db'), read_json=lambda _:CFG, ai_key=''))
    def q(price, stamp, received=None, session='regular', status='normal', currency='USD'):
        return Quote('NVDA',price,currency,'Longbridge OpenAPI',session,stamp,received or stamp+1,status)
    async def run():
        await engine.on_quote(q(101,1000))
        await engine.on_quote(q(99,1001,received=1200))  # stored, but stale cannot alter baseline
        await engine.on_quote(q(99,1001))  # duplicate stale cannot become fresh on retry
        await engine.on_quote(q(99,999))
        await engine.on_quote(q(99,1002,status='halted'))
        await engine.on_quote(q(99,1003,currency='JPY'))
        assert engine.db.due_alerts() == []
        await engine.on_quote(q(99,1004))
    asyncio.run(run())
    body = engine.db.due_alerts()[0]['body']
    assert 'USD' in body and '盘中' in body and 'Longbridge' in body
    assert len(engine.db.due_alerts()) == 1
