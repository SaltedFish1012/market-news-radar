import json
import pytest
from app.settings_store import SettingsUpdate
from test_settings import store


def test_x_defaults_and_username_dedupe(store):
    from app.settings_store import XAccount
    assert XAccount(username='@Example').username == 'Example'
    assert XAccount(username='Example').push_mode == 'filtered'
    assert not XAccount(username='Example').include_replies
    assert not store.snapshot()['values']['x_enabled']
    for accounts in [[{'username':'Example'},{'username':'@example'}],[{'username':'bad/name'}]]:
        with pytest.raises(ValueError): SettingsUpdate(revision=0,values={'x_accounts':accounts})
    for value in [59,3601]:
        with pytest.raises(ValueError): SettingsUpdate(revision=0,values={'x_poll_interval':value})


def test_x_secret_roundtrip_and_enable_requirements(store):
    with pytest.raises(ValueError,match='X'):
        store.prepare(SettingsUpdate(revision=0,values={'x_enabled':True}))
    candidate = store.prepare(SettingsUpdate(revision=0,values={'x_enabled':True,'x_accounts':[{'username':'Example','related_symbols':['005930.KS']}]},secrets={'x_bearer_token':'private-X-key'}))
    store.commit(candidate)
    assert store.settings().x_bearer_token == 'private-X-key'
    assert 'private-X-key' not in json.dumps(store.snapshot())
    assert 'private-X-key' not in store.redact('error private-X-key')
    assert store.settings(store.prepare(SettingsUpdate(revision=1,secrets={'x_bearer_token':''}))).x_bearer_token == 'private-X-key'
    with pytest.raises(ValueError):
        store.prepare(SettingsUpdate(revision=1,clear_secrets=['x_bearer_token']))
