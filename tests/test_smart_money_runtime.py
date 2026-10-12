import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.smart_money import collect_round, resolve_issuer_scope, sec_contact_ready, smart_money_loop
from app.sec_client import SECError
from app.smart_money_store import SmartMoneyStore
from tests.sec_helpers import make_ref
from test_x_pipeline import make_engine
from test_research import web_client

FIX=Path(__file__).parent/'fixtures'/'sec'
NOW=datetime(2026,10,12,tzinfo=timezone.utc).timestamp()


def configured(engine,**kw):
    return replace(engine.settings,smart_money_enabled=True,sec_contact='research@example.org',
        smart_money_institutions=[{'cik':'1067983','name':'Offline institution','enabled':True}],**kw)


def payload(refs,files=None):
    columns={k:[r[v] or '' for r in refs] for k,v in [('accessionNumber','accession'),('form','form'),('filingDate','filed_at'),('reportDate','report_period'),('primaryDocument','primary_document')]}
    return {'filings':{'recent':columns,'files':files or []}}


class Client:
    def __init__(self,refs=None): self.refs=refs or [make_ref()];self.calls=[];self.bad=set();self.closed=False
    async def ticker_index(self): self.calls.append('tickers');return {'NVDA':'1045810'}
    async def submissions(self,cik): self.calls.append('submissions:'+cik);return payload(self.refs if cik=='1067983' else [])
    async def submission_file(self,cik,name): self.calls.append(name);return payload([])['filings']['recent']
    async def filing_documents(self,ref):
        self.calls.append(ref['accession'])
        if ref['accession'] in self.bad: raise ValueError('damaged fixture')
        cover=(FIX/'13f_cover.xml').read_bytes()
        if ref.get('report_period')=='2026-03-31': cover=cover.replace(b'06-30-2026',b'03-31-2026')
        return cover,[(FIX/'13f_table.xml').read_bytes()]
    async def close(self): self.closed=True


def test_cancel_after_fetch_before_commit(tmp_path):
    engine=make_engine(tmp_path);config=configured(engine)
    class CancelClient(Client):
        async def filing_documents(self,ref):
            result=await super().filing_documents(ref);asyncio.current_task().cancel();return result
    async def run():
        task=asyncio.create_task(collect_round(engine,config,CancelClient(),now=NOW))
        with pytest.raises(asyncio.CancelledError): await task
        assert SmartMoneyStore(engine.db).filings()==[]
        assert engine.db.list_rows('news')==[] and engine.db.due_alerts()==[]
    asyncio.run(run())


def test_partial_failure_preserves_pending_work(tmp_path):
    engine=make_engine(tmp_path);config=configured(engine)
    current=make_ref();previous=make_ref(accession='0001067983-26-000003',filed_at='2026-05-14',report_period='2026-03-31')
    client=Client([current,previous]);client.bad.add(current['accession'])
    asyncio.run(collect_round(engine,config,client,now=NOW))
    store=SmartMoneyStore(engine.db);progress=store.progress('institution:1067983')
    assert [r['accession'] for r in progress['pending']]==[current['accession']]
    assert store.has_filing(previous['accession']) and progress['coverage_state']=='partial'
    # A fresh client sees only a new rolling discovery page, but failed work survives.
    resumed=Client([previous])
    asyncio.run(collect_round(engine,config,resumed,now=NOW+3601))
    assert current['accession'] in resumed.calls and len(store.filings())==2
    assert store.progress('institution:1067983')['coverage_state']=='complete'
    assert engine.db.due_alerts()==[] and engine.db.list_rows('news')==[]


def test_initial_coverage_and_scope(tmp_path):
    scope,unsupported=resolve_issuer_scope([
        {'symbol':s,'market':m,'asset_type':a} for s,m,a in [('NVDA','US','stock'),('NVDA.US','US','stock'),('BRK.B','US','stock'),('BRK-B','US','stock'),('SPY','US','etf'),('^GSPC','US','index'),('005930.KS','KR','stock'),('7203.T','JP','stock'),('600519.SS','CN','stock')]],
        {'NVDA':'1045810','BRK-B':'1067983'}, {'BRK.B':'1067983'})
    assert scope=={'1045810':['NVDA'],'1067983':['BRK.B','BRK-B']}
    assert len(unsupported)==5 and all(r['reason'] for r in unsupported)
    engine=make_engine(tmp_path);config=configured(engine)
    current=make_ref();old=make_ref(accession='0001067983-26-000004',report_period='2025-12-31',filed_at='2026-02-14')
    client=Client([current,old])
    asyncio.run(collect_round(engine,config,client,now=NOW))
    store=SmartMoneyStore(engine.db)
    assert old['accession'] not in client.calls
    state=store.progress('institution:1067983')
    assert state['coverage_start']=='2026-03-31' and state['coverage_state']=='partial'
    assert len(store.filings())==1


def test_insiders_initial_ninety_days_and_duplicate_issuer(tmp_path):
    engine=make_engine(tmp_path)
    engine.watchlist={'symbols':['NVDA','NVDX'],'instruments':[],'price_rules':[]}
    config=replace(configured(engine,smart_money_issuer_map={'NVDX':'1045810'}),smart_money_institutions=[])
    recent=make_ref(cik='1045810',form='4',accession='0001045810-26-000001',report_period='2026-08-12',primary_document='owner.xml',source_url='https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/owner.xml')
    old=recent|{'accession':'0001045810-26-000002','filed_at':'2026-07-01'}
    class InsiderClient(Client):
        async def submissions(self,cik): self.calls.append('submissions:'+cik);return payload([recent,old])
        async def filing_documents(self,ref):
            self.calls.append(ref['accession']);return (FIX/'form4.xml').read_bytes(),[]
    client=InsiderClient()
    asyncio.run(collect_round(engine,config,client,now=NOW))
    assert client.calls.count('submissions:1045810')==1 and old['accession'] not in client.calls
    assert SmartMoneyStore(engine.db).progress('issuer:1045810')['symbols']==['NVDA','NVDX']


def test_unknown_report_period_and_supplemental_discovery(tmp_path):
    engine=make_engine(tmp_path);config=configured(engine)
    current=make_ref(report_period=None)
    previous=make_ref(accession='0001067983-26-000003',report_period=None,filed_at='2026-05-14')
    old=make_ref(accession='0001067983-26-000004',report_period=None,filed_at='2026-02-14')
    class UnknownClient(Client):
        async def submissions(self,cik):
            return payload([current,old],[{'name':'CIK0001067983-submissions-001.json','filingTo':'2026-05-14'}]) if cik=='1067983' else payload([])
        async def submission_file(self,cik,name): self.calls.append(name);return payload([previous])['filings']['recent']
        async def filing_documents(self,ref):
            period={'0001067983-26-000001':'2026-06-30','0001067983-26-000003':'2026-03-31','0001067983-26-000004':'2025-12-31'}[ref['accession']]
            document,tables=await super().filing_documents(ref|{'report_period':'2026-03-31' if period=='2026-03-31' else None})
            if period=='2025-12-31':document=document.replace(b'06-30-2026',b'12-31-2025')
            return document,tables
    client=UnknownClient()
    asyncio.run(collect_round(engine,config,client,now=NOW))
    store=SmartMoneyStore(engine.db)
    assert {f['report_period'] for f in store.filings()}=={'2026-03-31','2026-06-30'}
    assert store.progress('institution:1067983')['coverage_state']=='complete'
    assert client.calls.count(current['accession'])==1


def test_failed_previous_queue_survives_new_quarter(tmp_path):
    engine=make_engine(tmp_path);config=configured(engine);store=SmartMoneyStore(engine.db)
    previous=make_ref(accession='0001067983-26-000003',report_period='2026-03-31',filed_at='2026-05-14',source_url='https://www.sec.gov/Archives/edgar/data/1067983/000106798326000003/primary_doc.xml')
    state=store.progress('institution:1067983');state.update(pending=[previous],initialized=True,coverage_end='2026-09-30',coverage_start='2026-06-30')
    store.save_progress(state)
    client=Client([make_ref()])
    asyncio.run(collect_round(engine,config,client,now=NOW))
    assert store.has_filing(previous['accession'])


def test_contact_missing_disabled_and_backoff(tmp_path,monkeypatch):
    engine=make_engine(tmp_path);client=Client()
    assert sec_contact_ready('Research <research@example.org>') and not sec_contact_ready('replace-contact')
    for config in [replace(configured(engine),smart_money_enabled=False),replace(configured(engine),sec_contact='')]:
        asyncio.run(collect_round(engine,config,client,now=NOW))
    assert client.calls==[]
    class RateClient(Client):
        async def ticker_index(self): self.calls.append('blocked');raise SECError('rate_limit',429)
    config=configured(engine);rate=RateClient();store=SmartMoneyStore(engine.db)
    asyncio.run(collect_round(engine,config,rate,now=NOW))
    assert store.progress('sec:cooldown')['retry_at']==NOW+30
    asyncio.run(collect_round(engine,config,rate,now=NOW+1))
    assert rate.calls==['blocked']
    asyncio.run(collect_round(engine,config,rate,now=NOW+31))
    assert store.progress('sec:cooldown')['retry_at']==NOW+91
    class HeaderClient(RateClient):
        async def ticker_index(self): raise SECError('permission',403,NOW+1800)
    asyncio.run(collect_round(engine,config,HeaderClient(),now=NOW+92))
    assert store.progress('sec:cooldown')['retry_at']==NOW+1800
    monkeypatch.setattr('app.smart_money.SECClient',lambda *a:pytest.fail('disabled loop created external client'))
    async def disabled():
        task=asyncio.create_task(smart_money_loop(engine,replace(config,smart_money_enabled=False)))
        await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(disabled())


def test_smart_settings_restart_only_smart_job(web_client,monkeypatch):
    import app.main as main
    client,token=web_client;client.post('/api/session',json={'token':token});events=[]
    async def stop(names=None): events.append(('stop',set(names or [])))
    monkeypatch.setattr(main,'stop_jobs',stop)
    monkeypatch.setattr(main,'start_jobs',lambda names=None:events.append(('start',set(names or []))))
    assert 'smart_money' in main.JOB_FUNCTIONS
    assert client.put('/api/settings',json={'revision':0,'values':{'smart_money_poll_interval':1200}}).status_code==200
    assert events==[('stop',{'smart_money'}),('start',{'smart_money'})]


def test_watchlist_and_shared_contact_restart_required_jobs(web_client,monkeypatch):
    import app.main as main
    client,token=web_client;client.post('/api/session',json={'token':token});events=[]
    async def stop(names=None): events.append(('stop',set(names or [])))
    monkeypatch.setattr(main,'stop_jobs',stop)
    monkeypatch.setattr(main,'start_jobs',lambda names=None:events.append(('start',set(names or []))))
    assert client.put('/api/settings',json={'revision':0,'watchlist':{'symbols':['AAPL'],'price_rules':[]}}).status_code==200
    assert events==[('stop',{'smart_money','longbridge_quote','longbridge_news'}),('start',{'smart_money','longbridge_quote','longbridge_news'})]
    events.clear()
    assert client.put('/api/settings',json={'revision':1,'values':{'sec_contact':'research@example.org'}}).status_code==200
    assert events==[('stop',{'smart_money','rss'}),('start',{'smart_money','rss'})]
