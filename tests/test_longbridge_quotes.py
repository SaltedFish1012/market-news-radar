import asyncio
from types import SimpleNamespace
from threading import Event
import app.connectors as connectors
from app.instruments import resolve_instrument
from app.market_sessions import Quote
from app.engine import Engine


def test_snapshot_and_push_preserve_all_sessions():
    assert hasattr(connectors, 'decode_longbridge_quotes'), 'quote decoder is missing'
    def data(price, timestamp, **kw):
        return SimpleNamespace(last_done=price,timestamp=timestamp,trade_status='Normal',**kw)
    snapshot = data(100,1000,pre_market_quote=data(99,900),post_market_quote=data(101,1100),over_night_quote=data(102,1200))
    quotes = connectors.decode_longbridge_quotes('NVDA.US',snapshot,resolve_instrument('NVDA'),1201)
    assert {q.session:q.price for q in quotes} == {'regular':100,'pre':99,'post':101,'overnight':102}
    assert connectors.longbridge_symbols({'symbols':['NVDA','005930.KS']}) == {'NVDA.US':'NVDA'}
    push = data(103,1300,trade_session='Overnight')
    assert connectors.decode_longbridge_quotes('NVDA.US',push,resolve_instrument('NVDA'),1301)[0].session == 'overnight'


def test_cancelled_generation_cannot_write(tmp_path):
    assert hasattr(connectors, 'deliver_longbridge_quote'), 'cancel-safe delivery is missing'
    engine = Engine(SimpleNamespace(db_path=str(tmp_path/'db'),read_json=lambda _:{'symbols':['NVDA']},ai_key=''))
    stopped = Event()
    quote = Quote('NVDA',100,'USD','LB','regular',1000,1001,'normal')
    async def run():
        task = asyncio.create_task(connectors.deliver_longbridge_quote(engine,quote,stopped))
        stopped.set()
        await task
    asyncio.run(run())
    assert engine.db.session_quotes() == [] and engine.db.due_alerts() == []
