import importlib.util
import json
from dataclasses import replace

import pytest

from app.config import Settings
from app.settings_store import SettingsStore, SettingsUpdate


def api():
    assert importlib.util.find_spec('app.instruments'), 'unified instrument model is missing'
    from app import instruments
    return instruments


def test_symbol_aliases_and_leading_zeros():
    model = api()
    assert model.normalize_symbol(' nvda.US ') == 'NVDA'
    assert model.resolve_instrument('005930.KS').currency == 'KRW'
    assert model.resolve_instrument('000001.SZ').timezone == 'Asia/Shanghai'
    assert model.resolve_instrument('7203.T').provider_symbols == {'yahoo': '7203.T'}
    assert model.resolve_instrument('^N225').asset_type == 'index'
    assert model.resolve_instrument('SPY').asset_type == 'etf'
    for invalid in ['../NVDA', 'NVDA/US', '005930', 'NVDA.US.US', 'NVDA?key=x', 'A\nB']:
        assert model.normalize_symbol(invalid) == ''


def test_metadata_conflicts():
    model = api()
    with pytest.raises(ValueError):
        model.normalize_instruments(['NVDA'], [{'symbol':'NVDA', 'currency':'JPY'}])
    with pytest.raises(ValueError):
        model.normalize_instruments(['NVDA'], [{'symbol':'NVDA'}, {'symbol':'NVDA.US', 'name':'other'}])
    with pytest.raises(ValueError):
        model.resolve_instrument('^N225', [{'symbol':'^N225', 'asset_type':'stock'}])
    with pytest.raises(ValueError):
        model.resolve_instrument('UNKNOWN.HK')


def test_old_settings_migrate_only_in_memory(tmp_path):
    base = replace(Settings(), db_path=str(tmp_path/'radar.db'),
        ai_enabled=False, ai_key='private-test-key', lb_enabled=False, lb_news_enabled=False,
        alpaca_news_enabled=False, telegram_token='', telegram_chat='',
        watchlist_override={'symbols':['NVDA'], 'price_rules':[{'symbol':'NVDA','kind':'price_above','value':120}]}, feeds_override=[])
    original = SettingsStore(base)
    original.commit(original.prepare(SettingsUpdate(revision=0)))
    saved = json.loads(original.path.read_text())
    saved['watchlist'].pop('instruments', None)
    for rule in saved['watchlist']['price_rules']:
        rule.pop('sessions', None)
    saved['values'].pop('lb_overnight_enabled', None)
    original.path.write_text(json.dumps(saved))
    before = original.path.read_bytes()
    restored = SettingsStore(base)
    snapshot = restored.snapshot()
    assert snapshot['watchlist']['instruments'][0]['currency'] == 'USD'
    assert snapshot['watchlist']['price_rules'][0]['sessions'] == ['pre','regular','post','overnight']
    assert snapshot['revision'] == 1 and restored.settings().ai_key == 'private-test-key'
    assert restored.path.read_bytes() == before
    restored.commit(restored.prepare(SettingsUpdate(revision=1)))
    assert 'instruments' in json.loads(restored.path.read_text())['watchlist']


def test_mixed_watchlist_and_rule_session_validation(tmp_path):
    base = replace(Settings(), db_path=str(tmp_path/'radar.db'), ai_enabled=False,
        lb_enabled=False, lb_news_enabled=False, alpaca_news_enabled=False,
        watchlist_override={'symbols':['NVDA']}, feeds_override=[])
    store = SettingsStore(base)
    update = SettingsUpdate(revision=0, watchlist={'symbols':['NVDA.US','005930.KS','^N225'],
        'price_rules':[{'symbol':'NVDA','kind':'price_above','value':120,'sessions':['overnight']}]})
    candidate = store.prepare(update)
    assert [i['market'] for i in candidate['watchlist']['instruments']] == ['US','KR','JP']
    with pytest.raises(ValueError):
        SettingsUpdate(revision=0, watchlist={'symbols':['NVDA'], 'price_rules':[
            {'symbol':'NVDA','kind':'price_above','value':120,'sessions':['unknown']}]})
