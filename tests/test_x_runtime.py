import asyncio
from email.utils import formatdate
import time
import httpx
import pytest
from test_x_pipeline import make_engine
from test_research import web_client


def test_rate_limits_and_backoff_persist():
    from app.x_news import XAPIError,retry_delay,XClient
    assert retry_delay(XAPIError('network'),1,1000)==5
    assert retry_delay(XAPIError('network'),20,1000)==300
    assert retry_delay(XAPIError('rate_limit',429),20,1000)==900
    assert retry_delay(XAPIError('rate_limit',429,2800),1,1000)==1800
    assert retry_delay(XAPIError('authentication',401),1,1000)>=300
    async def run():
        for headers in [{'Retry-After':'1800'},{'Retry-After':formatdate(time.time()+1800,usegmt=True)},{'x-rate-limit-reset':str(time.time()+1800)}]:
            client=XClient('SECRET',httpx.MockTransport(lambda r:httpx.Response(429,headers=headers,text='SECRET')))
            try:await client.resolve_user('Example')
            except XAPIError as exc:
                assert exc.kind=='rate_limit' and exc.retry_at>time.time()+1700 and 'SECRET' not in str(exc)
            else:assert False
            await client.close()
    asyncio.run(run())


def test_single_account_error_does_not_block_others(tmp_path):
    from app.x_news import XClient,poll_round
    engine=make_engine(tmp_path)
    def respond(request):
        if 'Bad' in request.url.path:return httpx.Response(401,json={})
        if '/by/username/' in request.url.path:return httpx.Response(200,json={'data':{'id':'42','username':'Good'}})
        return httpx.Response(200,json={'data':[]})
    async def run():
        client=XClient('SECRET',httpx.MockTransport(respond))
        await poll_round(engine,client,[{'username':'Bad','enabled':True},{'username':'Good','enabled':True}],now=1000)
        assert engine.db.x_cursor('username:bad')['retry_at']>=1300
        assert engine.db.x_cursor('42')['last_success_at']==1000
        await client.close()
    asyncio.run(run())


def test_x_settings_restart_only_x_job(web_client,monkeypatch):
    import app.main as main
    client,token=web_client;client.post('/api/session',json={'token':token});started=[]
    monkeypatch.setattr(main,'start_jobs',lambda names=None:started.extend(names or []))
    response=client.put('/api/settings',json={'revision':0,'values':{'x_poll_interval':180}})
    assert response.status_code==200 and started==['x_news']
    assert client.get('/api/status').json()['configured']['x_news'] is False


def test_cancel_returned_page_before_archive(tmp_path):
    from app.x_news import poll_account
    engine=make_engine(tmp_path)
    class Client:
        async def resolve_user(self,username):return {'id':'42','username':'Example'}
        async def fetch_posts(self,*args,**kwargs):
            asyncio.current_task().cancel()
            return {'data':[{'id':'100','text':'should not archive'}]}
    async def run():
        task=asyncio.create_task(poll_account(engine,Client(),{'username':'Example'},now=1000))
        with pytest.raises(asyncio.CancelledError):await task
        assert not engine.db.list_rows('news') and not engine.db.due_alerts()
    asyncio.run(run())
