import asyncio
import json
import os
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.ai_client import AIError, request_analysis
from app.config import Settings
from app.settings_store import SettingsStore, SettingsUpdate, ConfigConflict, PROVIDERS
from test_research import web_client


@pytest.fixture
def store(tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config/'watchlist.json').write_text(json.dumps({'symbols':['NVDA'], 'price_rules':[]}))
    (config/'feeds.json').write_text('[]')
    base = Settings(db_path=str(tmp_path/'radar.db'), config_dir=config, ai_provider='openai',
                    ai_key='private-openai-test-key', ai_enabled=False, alpaca_key='', alpaca_secret='',
                    alpaca_news_enabled=False, lb_enabled=False, lb_news_enabled=False,
                    telegram_token='', telegram_chat='', bark_key='')
    return SettingsStore(base)


def test_profiles_persist_independently_without_exposing_credentials(store):
    candidate = store.prepare(SettingsUpdate(revision=0, values={'ai_provider':'gemini', 'ai_model':'gemini-3.8-flash'},
                                             secrets={'ai_key':'private-gemini-test-key'}))
    assert store.settings(candidate).ai_base_url == PROVIDERS['gemini']['base_url']
    store.commit(candidate)
    candidate = store.prepare(SettingsUpdate(revision=1, values={'ai_provider':'openai'}))
    assert store.settings(candidate).ai_key == 'private-openai-test-key'
    store.commit(candidate)
    restored = SettingsStore(store.base)
    snapshot = restored.snapshot()
    assert snapshot['revision'] == 2
    assert snapshot['ai_profiles']['gemini']['key_set']
    assert 'private-' not in json.dumps(snapshot)
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    candidate = restored.prepare(SettingsUpdate(revision=2, values={'ai_provider':'gemini'}))
    assert restored.settings(candidate).ai_key == 'private-gemini-test-key'


def test_blank_preserves_key_and_explicit_clear_only_clears_selected_provider(store):
    candidate = store.prepare(SettingsUpdate(revision=0, secrets={'ai_key':''}))
    assert store.settings(candidate).ai_key == store.base.ai_key
    store.commit(candidate)
    candidate = store.prepare(SettingsUpdate(revision=1, clear_secrets=['ai_key']))
    assert store.settings(candidate).ai_key == ''
    assert store.settings().ai_key == store.base.ai_key  # No mutation before commit.
    with pytest.raises(ValueError, match='同时'):
        store.prepare(SettingsUpdate(revision=1, secrets={'ai_key':'new-key'}, clear_secrets=['ai_key']))


def test_atomic_disk_failure_keeps_previous_file_and_state(store, monkeypatch):
    store.commit(store.prepare(SettingsUpdate(revision=0)))
    previous = store.path.read_bytes()
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr('app.settings_store.os.replace', fail)
    with pytest.raises(OSError):
        store.commit(store.prepare(SettingsUpdate(revision=1, values={'rss_interval':60})))
    assert store.path.read_bytes() == previous
    assert store.snapshot()['revision'] == 1
    assert not list(store.path.parent.glob('.settings-*'))


def test_revision_conflict_and_enable_validation(store):
    with pytest.raises(ConfigConflict):
        store.prepare(SettingsUpdate(revision=1))
    with pytest.raises(ValueError, match='API Key'):
        store.prepare(SettingsUpdate(revision=0, values={'ai_provider':'gemini','ai_enabled':True}))
    with pytest.raises(ValueError, match='Alpaca'):
        store.prepare(SettingsUpdate(revision=0, values={'alpaca_news_enabled':True}))
    with pytest.raises(ValueError, match='Telegram'):
        store.prepare(SettingsUpdate(revision=0, secrets={'telegram_token':'bot-token'}))


@pytest.mark.parametrize('values', [
    {'ai_base_url':'http://remote.example/v1'}, {'ai_base_url':'https://key:secret@example.com/v1'},
    {'ai_base_url':'https://example.com/?key=secret'}, {'rss_interval':1},
    {'ai_max_requests_per_hour':601}, {'ai_model':'model\nheader'}, {'ai_provider':'unknown'},
])
def test_invalid_configuration_rejected(values):
    with pytest.raises(ValidationError):
        SettingsUpdate(revision=0, values=values)


def test_watchlist_rules_and_rss_queries(store):
    candidate = store.prepare(SettingsUpdate(revision=0,
        watchlist={'symbols':['nvda','NVDA'], 'price_rules':[{'symbol':'nvda','kind':'price_above','value':123}]},
        feeds=[{'name':'Query feed','url':'https://example.com/rss?q=AI&lang=en'}]))
    assert candidate['watchlist']['symbols'] == ['NVDA']
    assert candidate['feeds'][0]['url'].endswith('?q=AI&lang=en')
    with pytest.raises(ValueError, match='自选'):
        store.prepare(SettingsUpdate(revision=0, watchlist={'symbols':['NVDA'],
            'price_rules':[{'symbol':'MU','kind':'price_above','value':123}]}))
    with pytest.raises(ValueError, match='名称不能重复'):
        store.prepare(SettingsUpdate(revision=0, feeds=[{'name':'Same','url':'https://example.com/rss'}]*2))


RESULT = {'sentiment':'uncertain','affected_symbols':['nvda'], 'evidence_level':'unverified',
          'rationale':'测试分析', 'uncertainty':'合成事件'}


@pytest.mark.parametrize('provider', PROVIDERS)
def test_native_ai_protocol_and_schema(store, provider):
    config = replace(store.base, ai_provider=provider, ai_base_url=PROVIDERS[provider]['base_url'],
                     ai_model=PROVIDERS[provider]['models'][0] if PROVIDERS[provider]['models'] else 'deepseek-chat')
    def handle(request):
        body = json.loads(request.content)
        assert config.ai_key not in str(request.url) and config.ai_key not in request.content.decode()
        assert 'temperature' not in body
        content = json.dumps(RESULT)
        if provider == 'gemini':
            assert str(request.url).endswith('/models/gemini-3.8-flash:generateContent')
            assert request.headers['x-goog-api-key'] == config.ai_key
            assert body['generationConfig']['responseMimeType'] == 'application/json'
            assert 'systemInstruction' in body
            response = {'candidates':[{'content':{'parts':[{'text':content}]}}]}
        elif provider == 'anthropic':
            assert str(request.url).endswith('/messages')
            assert request.headers['x-api-key'] == config.ai_key
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert body['max_tokens'] > 0 and body['system']
            response = {'content':[{'type':'text','text':content}]}
        elif provider == 'openai':
            assert str(request.url).endswith('/responses')
            assert request.headers['authorization'] == 'Bearer '+config.ai_key
            assert body['store'] is False and body['text']['format']['type'] == 'json_object'
            response = {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':content}]}]}
        else:
            assert str(request.url).endswith('/chat/completions')
            assert body['model'] == 'deepseek-chat' and 'max_tokens' not in body
            response = {'choices':[{'message':{'content':'```json\n'+content+'\n```'}}]}
        return httpx.Response(200, json=response)
    result = asyncio.run(request_analysis(config, {'title':'测试消息'}, httpx.MockTransport(handle)))
    assert result['affected_symbols'] == ['NVDA']
    assert result['sentiment'] == 'uncertain'


def test_ai_failure_and_upstream_output_cannot_expose_key(store):
    config = replace(store.base, ai_provider='compatible')
    transport = httpx.MockTransport(lambda r: httpx.Response(401,json={'error':config.ai_key}))
    with pytest.raises(AIError) as error:
        asyncio.run(request_analysis(config, {}, transport))
    assert config.ai_key not in str(error.value)
    content = json.dumps({**RESULT, 'rationale':config.ai_key})
    result = asyncio.run(request_analysis(config, {}, httpx.MockTransport(lambda r: httpx.Response(200,
        json={'choices':[{'message':{'content':content}}]}))))
    assert result['rationale'] == '[REDACTED]'
    with pytest.raises(AIError, match='JSON'):
        asyncio.run(request_analysis(config, {}, httpx.MockTransport(lambda r: httpx.Response(200,
            json={'choices':[{'message':{'content':'{}'}}]}))))


def login(client, token):
    assert client.post('/api/session',json={'token':token}).status_code == 200


def test_settings_api_auth_save_hot_apply_and_conflict(web_client):
    import app.main as main
    client, token = web_client
    assert client.get('/api/settings').status_code == 401
    assert client.put('/api/settings',json={'revision':0}).status_code == 401
    login(client,token)
    old_rss = main.jobs['rss']
    main.engine.rss_warmups.add(('existing','https://example.com/rss'))
    response = client.put('/api/settings',json={'revision':0,
        'values':{'ai_provider':'gemini','ai_model':'gemini-3.8-flash'}, 'secrets':{'ai_key':'private-new-gemini-key'}})
    assert response.status_code == 200, response.text
    assert 'private-new-gemini-key' not in response.text
    assert main.jobs['rss'] is old_rss
    assert main.engine.rss_warmups
    assert main.settings.ai_provider == 'gemini' and main.engine.settings == main.settings
    assert client.put('/api/settings',json={'revision':0}).status_code == 409
    response = client.put('/api/settings',json={'revision':1,
        'values':{'market_provider':'demo','notifications_enabled':False},
        'watchlist':{'symbols':['MU','SPY'],'price_rules':[{'symbol':'MU','kind':'price_above','value':200}]}})
    assert response.status_code == 200, response.text
    assert client.get('/api/watchlist').json()['symbols'] == ['MU','SPY']
    assert main.engine.rules.rules[0]['symbol'] == 'MU'
    assert main.market.settings.market_provider == 'demo'
    assert client.get('/api/settings').headers['cache-control'] == 'no-store'
    restored = SettingsStore(main.config_store.base)
    assert restored.settings().ai_key == 'private-new-gemini-key'
    assert restored.settings().read_json('watchlist.json')['symbols'] == ['MU','SPY']


def test_draft_ai_test_does_not_save_or_change_live_settings(web_client, monkeypatch):
    import app.main as main
    client, token = web_client
    login(client,token)
    original = main.settings
    async def fake(config,event):
        assert config.ai_provider == 'gemini'
        assert config.ai_key == 'private-draft-key'
        assert event['source_symbols'] == [] and '测试' in event['title']
        return RESULT
    monkeypatch.setattr(main,'request_analysis',fake)
    response = client.post('/api/settings/test-ai',json={'revision':0,
        'values':{'ai_provider':'gemini','ai_model':'gemini-3.8-flash'},'secrets':{'ai_key':'private-draft-key'}})
    assert response.status_code == 200, response.text
    assert main.settings is original and main.config_store.snapshot()['revision'] == 0
    assert not main.config_store.path.exists()
    assert len(main.engine.ai_quota) == 1
    assert 'private-draft-key' not in response.text


def test_invalid_secret_not_echoed_in_validation_response(web_client):
    client, token = web_client
    login(client,token)
    secret = 'private secret with spaces'
    response = client.put('/api/settings',json={'revision':0,'secrets':{'ai_key':secret}})
    assert response.status_code == 422 and secret not in response.text
    assert 'input' not in response.json()['detail'][0]


def test_paused_notifications_preserve_pending_alerts(store, monkeypatch):
    from app.engine import Engine, notification_loop
    engine = Engine(replace(store.base, notifications_enabled=False))
    engine.db.add_alert('paused-test','测试','仅测试，不是市场事件')
    async def stop(_):
        raise asyncio.CancelledError
    monkeypatch.setattr('app.engine.asyncio.sleep',stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notification_loop(engine,engine.settings))
    pending = engine.db.due_alerts()
    assert len(pending) == 1 and pending[0]['attempts'] == 0
    engine.db.conn.close()


def test_notification_failure_does_not_store_credential_urls(store, monkeypatch):
    from app.engine import Engine, notification_loop
    config = replace(store.base, notifications_enabled=True, telegram_token='private-bot-token',telegram_chat='123')
    engine = Engine(config)
    engine.db.add_alert('failure-test','测试','仅测试，不是市场事件')
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            response = httpx.Response(401,request=httpx.Request('POST',url))
            response.raise_for_status()
    monkeypatch.setattr('app.engine.httpx.AsyncClient',lambda **kwargs:FakeClient())
    async def stop(_): raise asyncio.CancelledError
    monkeypatch.setattr('app.engine.asyncio.sleep',stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notification_loop(engine,config))
    error = engine.db.list_rows('alerts')[0]['last_error']
    assert '401' in error and config.telegram_token not in error
    assert config.telegram_token not in str(engine.db.list_rows('health'))
    engine.db.conn.close()


def test_settings_page_visible_before_js_and_assets_versioned(web_client):
    import hashlib
    import re
    from app.main import ASSETS
    client, _ = web_client
    root = client.get('/')
    page = client.get('/settings')
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
    assert 'id="settingsView" data-page="settings" hidden' in root.text
    assert 'id="settingsView" data-page="settings" hidden' not in page.text
    assert 'id="workspaceView" hidden' in page.text
    assert 'href="/settings"' in page.text
    for name in ('settings.js','navigation.js','dashboard.js','dashboard.css'):
        expected = hashlib.sha256((ASSETS/name).read_bytes()).hexdigest()[:12]
        assert f'/assets/{name}?v={expected}' in page.text
        assert client.get(f'/assets/{name}?v={expected}').status_code == 200


@pytest.mark.parametrize('path,expected', [('/', 'market'),('/strategies','strategies'),('/news','news'),
                                         ('/alerts','alerts'),('/sources','sources'),('/settings','settings')])
def test_each_workspace_url_has_one_visible_page_and_real_navigation(web_client,path,expected):
    from html.parser import HTMLParser
    class Pages(HTMLParser):
        def __init__(self):
            super().__init__()
            self.pages = {}
            self.links = []
        def handle_starttag(self,tag,attrs):
            attrs = dict(attrs)
            if 'data-page' in attrs:
                self.pages[attrs['data-page']] = 'hidden' not in attrs
            if 'data-section' in attrs:
                self.links.append((tag,attrs.get('href'),attrs.get('aria-current')))
    client, _ = web_client
    response = client.get(path)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    parsed = Pages();parsed.feed(response.text)
    assert list(key for key,visible in parsed.pages.items() if visible) == [expected]
    assert len(parsed.pages) == 6
    assert {url for tag,url,_ in parsed.links} == {'/','/strategies','/news','/alerts','/sources','/settings'}
    assert all(tag == 'a' for tag,_,_ in parsed.links)
    assert sum(current == 'page' for _,_,current in parsed.links) == 1
