from datetime import datetime, timezone
from app.feeds import parse_feed, parse_dt
from app.signals import classify, PriceRules
from app.db import DB

CFG={'symbols':['WDC','MU','NVDA'], 'theme_keywords':['OpenAI','CPI'],
     'company_map':{'openai':['NVDA','MU']},
     'price_rules':[{'symbol':'WDC','kind':'price_below','value':100},
                    {'symbol':'MU','kind':'change_5m_below_pct','value':-2}]}

def test_related_event():
    level,syms,themes=classify('OpenAI revenue misses estimates','Revenue revised',[],CFG)
    assert level=='critical' and 'NVDA' in syms and 'OpenAI' in themes

def test_unrelated_event_suppressed():
    lvl,_,_=classify('Small firm hires VP','Company names executive',[],CFG)
    assert lvl=='info'

def test_rss():
    raw='<rss><channel><item><title>Chip demand rising</title><link>https://ex.com/a</link><description>report</description><pubDate>Fri, 09 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
    out=parse_feed(raw,'test')
    assert out[0]['title']=='Chip demand rising' and out[0]['published_at'].startswith('2026-10-09')

def test_atom():
    raw='<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Filing 8-K</title><link href="https://example.com/f"/><updated>2026-10-09T01:00:00Z</updated></entry></feed>'
    out=parse_feed(raw,'SEC')
    assert out[0]['url']=='https://example.com/f'

def test_price_crossing():
    rule=PriceRules(CFG)
    assert rule.feed('WDC',101,now=1000,session='regular')==[]
    assert len(rule.feed('WDC',99,now=1001,session='regular'))==1
    assert rule.feed('WDC',98,now=1002,session='regular')==[]

def test_db_dedupe_and_outbox(tmp_path):
    db=DB(str(tmp_path/'test.db'))
    event={'event_key':'one','source':'test','title':'Hi','summary':'','url':'','published_at':None,
           'received_at':'2026-10-09T00:00:00+00:00','source_symbols':[],'related_symbols':[],'priority':'info'}
    assert db.put_news(event)==1
    assert db.put_news(event) is None
    assert db.add_alert('alert:1','Test','Hello')
    assert not db.add_alert('alert:1','Test','Hello')
    row=db.due_alerts()[0]
    db.alert_result(row['id'],True)
    assert db.due_alerts()==[]

import asyncio
from types import SimpleNamespace
from pathlib import Path
from app.engine import Engine


def test_news_to_alert_pipeline(tmp_path):
    settings=SimpleNamespace(db_path=str(tmp_path/'events.db'),
       read_json=lambda name: CFG if name=='watchlist.json' else [],
       news_max_age_minutes=25,ai_key='')
    engine=Engine(settings)
    now=datetime.now(timezone.utc).isoformat()
    message={'title':'OpenAI revenue misses estimates','summary':'Media report on AI spending',
             'source':'Demo Source','url':'https://example.test/a',
             'source_id':'demo-1','published_at':now,'source_symbols':[]}
    assert asyncio.run(engine.ingest(message))
    assert not asyncio.run(engine.ingest(message))
    assert len(engine.db.due_alerts())==1
    assert '尚未交叉核验' in engine.db.due_alerts()[0]['body']


def test_quote_to_alert_pipeline(tmp_path):
    settings=SimpleNamespace(db_path=str(tmp_path/'quotes.db'),
       read_json=lambda name: CFG if name=='watchlist.json' else [],
       news_max_age_minutes=25,ai_key='')
    engine=Engine(settings)
    async def run():
        from app.market_sessions import Quote
        await engine.on_quote(Quote('WDC',101,'USD','LB','regular',1000,1001,'normal'))
        await engine.on_quote(Quote('WDC',99,'USD','LB','regular',1001,1002,'normal'))
    asyncio.run(run())
    assert len(engine.db.due_alerts())==1
    assert engine.db.list_rows('quotes')[0]['symbol']=='WDC'
