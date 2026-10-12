import asyncio
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from app.config import Settings
from app.engine import Engine


def make_engine(tmp_path):
    return Engine(replace(Settings(),db_path=str(tmp_path/'test.db'),ai_key='',ai_enabled=False,
        watchlist_override={'symbols':['NVDA'],'theme_keywords':[],'price_rules':[]},feeds_override=[]))


def post(identity='100',text='ordinary'):
    return {'post_id':identity,'edit_history_ids':['100',identity],'raw_text':text,
            'author_id':'50','username':'Example','author_name':'作者',
            'created_at':datetime.now(timezone.utc).isoformat(),'references':[]}


def test_all_mode_pushes_info_once(tmp_path):
    engine=make_engine(tmp_path);account={'username':'Example','push_mode':'all','keywords':[]}
    asyncio.run(engine.ingest_x(post(text='<b>&amp;</b>'),account))
    asyncio.run(engine.ingest_x(post(text='<b>&amp;</b>'),account))
    assert len(engine.db.due_alerts())==1 and engine.db.due_alerts()[0]['title']=='X 用户言论 · @Example'
    assert engine.db.list_rows('news')[0]['raw_text']=='<b>&amp;</b>'
    asyncio.run(engine.ingest_x(post('101','edited'),account))
    assert len(engine.db.due_alerts())==1 and engine.db.list_rows('news')[0]['raw_text']=='edited'


def test_filtered_full_text_old_and_bindings(tmp_path):
    from app.x_events import x_should_push
    assert x_should_push({'raw_text':'a'*1600+'降息'},{'push_mode':'filtered','keywords':['降息']},{},'info')
    assert not x_should_push({'raw_text':'普通'},{'push_mode':'filtered','keywords':[]},{'theme_keywords':[]},'info')
    for index,stamp in enumerate([None,'invalid',(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()]):
        engine=make_engine(tmp_path / str(index));p=post();p['created_at']=stamp
        asyncio.run(engine.ingest_x(p,{'push_mode':'all','related_symbols':['NVDA']}))
        assert not engine.db.due_alerts() and engine.db.list_rows('news')[0]['priority']=='info'
