import json
import sqlite3
import pytest
from app.db import DB, utcnow


def event(post_id='100', history=None, text=None):
    return dict(event_key='x:100',source='X 用户言论',title='X 用户言论 · @Example',summary='摘要',url='https://x.com/Example/status/'+post_id,
        published_at=utcnow(),received_at=utcnow(),source_symbols=[],related_symbols=[],priority='info',
        source_type='x',raw_text=text or '<b>&amp;</b>'+('原文'*4000),
        x_metadata={'post_id':post_id,'edit_history_ids':history or [post_id],'author_id':'50','username':'Example'},edited_at=None)


def test_edit_chain_keeps_one_news_and_first_alert(tmp_path):
    path=str(tmp_path/'radar.db');db=DB(path)
    alert=dict(key='x:100:fast',title='X 用户言论',body='first',severity='info')
    news_id,state=db.store_x_event(event(),alert)
    assert state == 'inserted' and db.store_x_event(event(),alert)==(news_id,'duplicate')
    assert db.list_rows('news')[0]['raw_text'].startswith('<b>&amp;</b>')
    edited=event('101',['100','101'],'edited')
    assert db.store_x_event(edited,alert)==(news_id,'updated')
    assert db.store_x_event(event(),alert)==(news_id,'duplicate')
    assert len(db.list_rows('news'))==1 and len(db.due_alerts())==1
    assert db.due_alerts()[0]['body']=='first' and db.list_rows('news')[0]['raw_text']=='edited'
    db.save_x_user({'id':'50','username':'Renamed','name':'Author'},'Example')
    db.save_x_cursor('50',{'initialized':True,'since_id':'101','next_token':'page2'})
    db.conn.close();db=DB(path)
    assert db.x_user('example')['id']=='50' and db.x_user('Renamed')['id']=='50'
    assert db.x_cursor('50')['next_token']=='page2'


def test_x_event_and_outbox_are_atomic(tmp_path):
    db=DB(str(tmp_path/'test.db'))
    db.conn.execute("CREATE TRIGGER reject_alert BEFORE INSERT ON alerts BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        db.store_x_event(event(),dict(key='x:100:fast',title='X',body='first',severity='info'))
    assert not db.list_rows('news')
    assert not db.conn.execute('SELECT * FROM x_post_aliases').fetchall()
