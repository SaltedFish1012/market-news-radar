import asyncio
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import httpx
import pytest
from app.sec_client import SECClient, SECError, filing_refs
from app.sec_xml import MAX_RESPONSE_BYTES
from tests.sec_helpers import make_ref

FIX=Path(__file__).parent/'fixtures'/'sec'


def test_serial_rate_limit_and_retry_after():
    async def run():
        now=[1800000000.0]; starts=[]
        async def sleep(delay): now[0]+=delay
        def reply(request):
            starts.append(now[0])
            assert 'research@example.org' in request.headers['User-Agent']
            return httpx.Response(200,json={'filings':{'recent':{}}})
        client=SECClient('research@example.org',httpx.MockTransport(reply),clock=lambda:now[0],sleep=sleep)
        await asyncio.gather(*(client.submissions('1') for _ in range(3)))
        assert starts==[1800000000,1800000000.5,1800000001]
        await client.close()
        for status,header in [(403,'1800'),(429,format_datetime(datetime.fromtimestamp(now[0]+1800,timezone.utc),usegmt=True))]:
            client=SECClient('research@example.org',httpx.MockTransport(lambda r:httpx.Response(status,headers={'Retry-After':header},text='research@example.org SECRET')),clock=lambda:now[0])
            with pytest.raises(SECError) as caught: await client.submissions('1')
            assert caught.value.retry_at==now[0]+1800
            assert caught.value.status_code==status
            assert 'example.org' not in str(caught.value) and 'SECRET' not in str(caught.value)
            await client.close()
    asyncio.run(run())


def test_fixed_hosts_paths_and_response_limits():
    async def run():
        calls=[]
        def reply(request):
            calls.append(str(request.url))
            return httpx.Response(200,json={})
        client=SECClient('research@example.org',httpx.MockTransport(reply))
        for cik in ['0','../1','https://evil.test','１２３']:
            with pytest.raises((ValueError,SECError)): await client.submissions(cik)
        for name in ['../a.json','https://evil.test/a.json','CIK0000000002-submissions-001.json']:
            with pytest.raises((ValueError,SECError)): await client.submission_file('1',name)
        for ref in [make_ref(accession='../x'),make_ref(primary_document='../x.xml'),make_ref(primary_document='https://evil.test/x.xml')]:
            with pytest.raises((ValueError,SECError)): await client.filing_documents(ref)
        assert calls==[]
        await client.submissions('1')
        assert calls==['https://data.sec.gov/submissions/CIK0000000001.json']
        await client.close()
        for status,content,kind in [(301,b'', 'upstream'),(404,b'', 'not_found'),(403,b'', 'permission'),(429,b'', 'rate_limit'),(200,b'x'*(MAX_RESPONSE_BYTES+1),'response_too_large')]:
            client=SECClient('research@example.org',httpx.MockTransport(lambda r:httpx.Response(status,content=content,headers={'Location':'https://evil.test/'})))
            with pytest.raises(SECError) as caught: await client.submissions('1')
            assert caught.value.kind==kind
            assert caught.value.retry_at is None
            await client.close()
        manifest={'directory':{'item':[{'name':f'table{i}.xml'} for i in range(51)]}}
        client=SECClient('research@example.org',httpx.MockTransport(lambda r:httpx.Response(200,json=manifest) if r.url.path.endswith('index.json') else httpx.Response(200,content=(FIX/'13f_cover.xml').read_bytes())),sleep=lambda _:asyncio.sleep(0))
        with pytest.raises(SECError,match='too_many_documents'): await client.filing_documents(make_ref())
        await client.close()
    asyncio.run(run())


def test_recent_and_supplemental_submissions():
    recent=json.loads((FIX/'submissions.json').read_text())
    older=json.loads((FIX/'submissions_older.json').read_text())
    rows=filing_refs(recent,'0001067983')
    assert rows[0]['form']=='13F-HR'
    assert rows[0]['filed_at']=='2026-08-14' and rows[0]['report_period']=='2026-06-30'
    assert rows[1]['form']=='13F-NT'
    assert filing_refs(older,'1067983')[0]['report_period']=='2026-03-31'
    recent['filings']['recent']['form'].pop()
    with pytest.raises(SECError,match='invalid_response'): filing_refs(recent,'1067983')


def test_filing_documents_discover_multiple_tables():
    async def run():
        calls=[]
        def reply(request):
            path=request.url.path;calls.append(path)
            if path.endswith('index.json'): return httpx.Response(200,content=(FIX/'filing_index.json').read_bytes())
            if path.endswith('primary_doc.xml'): return httpx.Response(200,content=(FIX/'13f_cover.xml').read_bytes())
            if path.endswith('owner.xml'): return httpx.Response(200,content=(FIX/'form4.xml').read_bytes())
            if path.endswith('.xml'): return httpx.Response(200,content=b'<informationTable/>')
            if path.endswith('company_tickers.json'): return httpx.Response(200,content=(FIX/'company_tickers.json').read_bytes())
            raise AssertionError(path)
        client=SECClient('research@example.org',httpx.MockTransport(reply),sleep=lambda _:asyncio.sleep(0))
        cover,tables=await client.filing_documents(make_ref(primary_document='xslForm13F_X02/primary_doc.xml'))
        assert len(tables)==2 and b'edgarSubmission' in cover
        assert any(p.endswith('different_table.xml') for p in calls)
        form,tables=await client.filing_documents(make_ref(form='4',primary_document='xslF345X05/owner.xml'))
        assert b'ownershipDocument' in form and tables==[]
        assert not any('/xsl' in p for p in calls)
        assert await client.ticker_index()=={'BRK-B':'1067983','NVDA':'1045810'}
        await client.close()
    asyncio.run(run())

