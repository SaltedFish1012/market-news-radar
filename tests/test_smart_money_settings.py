from dataclasses import replace
import json
import pytest
from app.config import Settings
from app.settings_store import SettingsStore,SettingsUpdate


def store(tmp_path):
    return SettingsStore(replace(Settings(),db_path=str(tmp_path/'test.db'),x_enabled=False,x_bearer_token='SECRET-X',
        ai_enabled=False,ai_key='',lb_enabled=False,lb_news_enabled=False,sec_contact=''))


def test_smart_defaults_and_old_saved_settings(tmp_path):
    initial=store(tmp_path)
    assert initial.settings().smart_money_enabled is False
    assert initial.settings().smart_money_poll_interval==900
    old=initial.state.copy();old['values']=old['values'].copy()
    for key in list(old['values']):
        if key.startswith('smart_money_'):del old['values'][key]
    initial.path.write_text(json.dumps(old),encoding='utf-8')
    restored=store(tmp_path)
    assert restored.snapshot()['values']['smart_money_poll_interval']==900
    assert 'SECRET-X' not in json.dumps(restored.snapshot())


@pytest.mark.parametrize('value,valid',[(300,True),(86400,True),(299,False),(86401,False)])
def test_interval_boundaries(tmp_path,value,valid):
    current=store(tmp_path)
    if valid:
        candidate=current.prepare(SettingsUpdate(revision=0,values={'smart_money_poll_interval':value,'smart_money_enabled':True}))
        assert current.settings(candidate).smart_money_poll_interval==value
        assert current.settings(candidate).smart_money_enabled is True
    else:
        with pytest.raises(ValueError):SettingsUpdate(revision=0,values={'smart_money_poll_interval':value})


def test_institutions_and_explicit_maps_validate(tmp_path):
    current=store(tmp_path)
    institutions=[{'name':f'Fixture {i}','cik':str(i+1)} for i in range(20)]
    candidate=current.prepare(SettingsUpdate(revision=0,values={'smart_money_institutions':institutions,
        'smart_money_cusip_map':{'67066G104':'NVDA'},'smart_money_issuer_map':{'BRK.B':'0001067983'}}))
    assert len(current.settings(candidate).smart_money_institutions)==20
    assert current.settings(candidate).smart_money_issuer_map=={'BRK.B':'1067983'}
    for values in [dict(smart_money_institutions=institutions+[{'name':'Extra','cik':'21'}]),
        dict(smart_money_institutions=[{'name':'A','cik':'1'},{'name':'B','cik':'0001'}]),
        dict(smart_money_institutions=[{'name':'A','cik':'../evil'}]),
        dict(smart_money_cusip_map={'bad':'NVDA'}),dict(smart_money_cusip_map={'67066G104':'005930.KS'}),
        dict(smart_money_issuer_map={'^GSPC':'1'})]:
        with pytest.raises(ValueError):SettingsUpdate(revision=0,values=values)
