import json
from dataclasses import replace

import httpx
import pytest

from app.db import DB, utcnow
from app.x_news import XClient
from test_research import web_client
from test_x_storage import event


def snapshot(db):
    return {t: [tuple(r) for r in db.conn.execute('SELECT * FROM '+t)]
            for t in ('news','x_users','x_user_aliases','x_cursors','x_post_aliases','alerts')}


def test_news_filter_precedes_limit(tmp_path):
    db = DB(str(tmp_path/'test.db'))
    for i in range(2):
        db.put_news(dict(event_key=f'rss:{i}',source='RSS',title='ordinary',summary='',url='',
                        received_at=utcnow(),source_symbols=[],related_symbols=[],priority='info'))
    for i in range(150):
        db.store_x_event(event(str(100+i)))
    assert len(db.news_rows(limit=2,source_type='news')) == 2
    assert all(r['source_type'] != 'x' for r in db.news_rows(limit=2,source_type='news'))
    assert len(db.x_posts()) == 100


def test_x_archive_uses_confirmed_identity(tmp_path):
    db = DB(str(tmp_path/'test.db'))
    db.store_x_event(event())
    db.save_x_user({'id':'50','username':'Renamed','name':'Author'},'OldName')
    assert [r['event_key'] for r in db.x_posts(username='@OldName')] == ['x:100']
    assert db.x_posts(username='Unconfirmed') == []
    db.save_x_user({'id':'51','username':'OldName','name':'Other'},'OldName')
    assert db.x_posts(username='OldName') == []
    assert len(db.x_posts(username='Renamed')) == 1
    db.conn.execute("UPDATE news SET x_metadata='invalid' WHERE event_key='x:100'")
    assert db.x_posts(username='Renamed') == []


@pytest.mark.parametrize('status',[200,402,429])
def test_preview_is_read_only(web_client,monkeypatch,status):
    import app.main as main
    client,token = web_client
    client.post('/api/session',json={'token':token})
    monkeypatch.setattr(main,'settings',replace(main.settings,x_bearer_token='SECRET-X'))
    before = snapshot(main.engine.db)
    raw = '<script>speech</script>'+'a'*8000
    requests = []
    def respond(request):
        requests.append(request)
        if '/by/username/' in request.url.path:
            return httpx.Response(200,json={'data':{'id':'42','username':'aleabitoreddit','name':'Serenity'}})
        assert request.url.params['max_results']=='10'
        if status != 200:
            return httpx.Response(status,text='SECRET-X',headers={'Retry-After':'1800'})
        return httpx.Response(200,json={'data':[{'id':'101','author_id':'42','text':'short',
            'note_post':{'text':raw},'created_at':'2026-10-12T00:00:00Z'}]})
    upstream = XClient('SECRET-X',httpx.MockTransport(respond))
    monkeypatch.setattr(main,'XClient',lambda token:upstream)
    response = client.post('/api/x/preview',json={'username':'@aleabitoreddit'})
    assert response.status_code == 200
    body=response.json()
    if status==200:
        assert body['state']=='ok' and body['user']['id']=='42'
        assert body['items'][0]['raw_text']==raw
        assert body['items'][0]['url']=='https://x.com/aleabitoreddit/status/101'
    else:
        assert body['state']=='error'
        assert body['kind']==('billing' if status==402 else 'rate_limit')
        assert 'SECRET-X' not in response.text
    assert snapshot(main.engine.db)==before
    assert len(requests)==2 and upstream.http.is_closed


def test_preview_validation_auth_and_missing_token(web_client,monkeypatch):
    import app.main as main
    client,token=web_client
    assert client.get('/api/x/posts').status_code==401
    assert client.post('/api/x/preview',json={'username':'aleabitoreddit'}).status_code==401
    client.post('/api/session',json={'token':token})
    monkeypatch.setattr(main,'settings',replace(main.settings,x_bearer_token=''))
    monkeypatch.setattr(main,'XClient',lambda token:pytest.fail('must not request upstream'))
    assert client.post('/api/x/preview',json={'username':'https://evil.test'}).status_code==422
    assert client.post('/api/x/preview',json={'username':'valid','url':'https://evil.test'}).status_code==422
    body=client.post('/api/x/preview',json={'username':'aleabitoreddit'}).json()
    assert body['state']=='not_configured' and body['items']==[]
    assert client.get('/api/x/posts?limit=101').status_code==422
    assert client.get('/api/x/posts?username=../../oops').status_code==422
    assert client.get('/api/news?source_type=news').json()['items']==[]
    assert client.get('/api/x/posts').json()=={'items':[],'accounts':[]}
