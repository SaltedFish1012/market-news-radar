import sqlite3
from app.db import DB
from test_market_sessions import model


def test_repeatable_quote_migration_and_order(tmp_path):
    m = model()
    path = str(tmp_path/'quotes.db')
    db = DB(path)
    db.quote('NVDA',90,'legacy')
    db.conn.close()
    db = DB(path)
    old = db.session_quotes('NVDA')
    assert len(old) == 1 and old[0]['session'] == 'unknown' and old[0]['quote_at'] is None
    assert db.put_session_quote(m.Quote('NVDA',100,'USD','LB','regular',1000,1001,'normal'))
    assert not db.put_session_quote(m.Quote('NVDA',110,'USD','LB','regular',1000,1002,'normal'))
    assert not db.put_session_quote(m.Quote('NVDA',110,'USD','LB','regular',999,1002,'normal'))
    assert db.put_session_quote(m.Quote('NVDA',95,'USD','LB','pre',900,1002,'normal'))
    assert db.list_rows('quotes')[0]['price'] == 100  # 较早盘前快照不能覆盖主价
    db.conn.close()
    db = DB(path)
    assert len(db.session_quotes('NVDA')) == 3
    assert db.session_quotes('NVDA')[1]['currency'] == 'USD'
    db.conn.close()


def test_session_migration_preserves_existing_news_and_alerts(tmp_path):
    m = model()
    path = str(tmp_path/'quotes.db')
    db = DB(path)
    db.add_alert('original','title','body')
    db.conn.close()
    for _ in range(2):
        db = DB(path)
        assert db.list_rows('alerts')[0]['body'] == 'body'
        assert db.session_quotes() == []
        db.conn.close()
