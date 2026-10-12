import json
from dataclasses import replace
import pytest
from app.smart_money_store import SmartMoneyStore
from tests.sec_helpers import make_filing,make_position,make_transaction,make_progress
from test_research import web_client


def seed():
    import app.main as main
    store=SmartMoneyStore(main.engine.db)
    for i in range(3):
        acc=f'0001067983-26-{i+1:06d}'
        filing=make_filing(form='4/A' if i==2 else '4',accession=acc,report_period='2026-08-12',source_url=f'https://www.sec.gov/Archives/edgar/data/1067983/{acc.replace("-","")}/owner.xml')
        store.commit_filing(filing,[],[make_transaction(code='S' if i==0 else 'P')],make_progress(source_key='issuer:1067983',symbols=['NVDA']))
    store.commit_filing(make_filing(accession='0001067983-26-000004',source_url='https://www.sec.gov/Archives/edgar/data/1067983/000106798326000004/primary_doc.xml'),
        [make_position(),make_position(row_key='other',cusip='999999999',issuer_name='Unknown security')],[],make_progress(last_error='invalid_filing',coverage_state='partial'))
    main.settings=replace(main.settings,smart_money_cusip_map={'67066G104':'NVDA'},sec_contact='private-research@example.org')
    return store


def test_filters_precede_pagination(web_client):
    client,token=web_client;client.post('/api/session',json={'token':token});seed()
    body=client.get('/api/smart-money/insiders?symbol=NVDA&code=P&limit=1').json()
    assert body['total']==2 and len(body['items'])==1
    assert body['items'][0]['code']=='P' and body['items'][0]['symbols']==['NVDA']
    assert body['items'][0]['amended'] is True and body['items'][0]['active_buy_sell'] is False
    holdings=client.get('/api/smart-money/holdings?symbol=NVDA&limit=1').json()
    assert holdings['total']==1 and holdings['items'][0]['symbol']=='NVDA'
    assert holdings['reports'][0]['comparison_state']=='baseline_missing'
    assert 'items' not in holdings['reports'][0]


def test_api_auth_and_validation(web_client):
    client,token=web_client
    for path in ['overview','holdings','insiders']:
        assert client.get('/api/smart-money/'+path).status_code==401
    client.post('/api/session',json={'token':token});seed()
    for path in ['holdings?limit=0','holdings?offset=-1','holdings?cik=0','holdings?cik=../1','holdings?period=2026-06-31','holdings?period=2026-05-31','insiders?code=PS','insiders?code=Ｐ','insiders?symbol=<bad>','insiders?limit=501']:
        assert client.get('/api/smart-money/'+path).status_code==422,path
    for path in ['holdings?cik=999','holdings?symbol=ZZZZ','insiders?symbol=ZZZZ']:
        response=client.get('/api/smart-money/'+path)
        assert response.status_code==200 and response.json()['total']==0


def test_archives_read_when_disabled_and_partial(web_client):
    client,token=web_client;client.post('/api/session',json={'token':token});seed()
    import app.main as main
    main.settings=replace(main.settings,sec_contact='',smart_money_enabled=False)
    overview=client.get('/api/smart-money/overview').json()
    assert overview['configured']=={'enabled':False,'contact_configured':False}
    assert overview['counts']=={'filings':4,'holdings':2,'insiders':3}
    institution=next(s for s in overview['sources'] if s['source_key']=='institution:1067983')
    assert institution['coverage_state']=='partial' and institution['last_error']=='invalid_filing'
    assert 'pending' not in institution
    assert client.get('/api/smart-money/holdings').json()['total']==2


def test_api_never_fetches_upstream_or_exposes_contact(web_client,monkeypatch):
    client,token=web_client;client.post('/api/session',json={'token':token});seed()
    def forbidden(*a,**kw):pytest.fail('read-only API fetched upstream')
    monkeypatch.setattr('app.sec_client.SECClient.__init__',forbidden)
    for path in ['overview','holdings','insiders']:
        response=client.get('/api/smart-money/'+path)
        assert response.status_code==200
        assert 'private-research@example.org' not in response.text and token not in response.text
    response=client.get('/api/status')
    assert 'smart_money' in response.json() and 'private-research@example.org' not in response.text
