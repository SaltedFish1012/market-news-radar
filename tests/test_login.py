import time
from dataclasses import replace
from http.cookies import SimpleCookie

import pytest
from fastapi import HTTPException

from test_research import web_client


def cookie(response):
    parsed = SimpleCookie()
    parsed.load(response.headers['set-cookie'])
    return parsed['radar_session']


def test_remember_login_sets_persistent_cookie_with_matching_server_expiry(web_client):
    import app.main as main
    client, token = web_client
    now = int(time.time())
    response = client.post('/api/session', json={'token':token,'remember':True})
    assert response.status_code == 200
    session = cookie(response)
    assert int(session['max-age']) == 30*24*3600
    assert session['expires'] and session['httponly'] and session['samesite'] == 'strict'
    assert token not in session.value
    expiry = int(session.value.split('.')[0])
    assert now+main.REMEMBER_SESSION_TTL_SECONDS <= expiry <= int(time.time())+main.REMEMBER_SESSION_TTL_SECONDS
    assert response.json()['expires_at'] == expiry and response.json()['remember'] is True
    assert client.get('/api/settings').status_code == 200


@pytest.mark.parametrize('remember', [None, False])
def test_temporary_login_retains_twelve_hour_limit_without_persistent_cookie(web_client, remember):
    import app.main as main
    client, token = web_client
    body = {'token':token}
    if remember is not None:
        body['remember'] = remember
    now = int(time.time())
    response = client.post('/api/session',json=body)
    session = cookie(response)
    assert response.status_code == 200
    assert session['max-age'] == '' and session['expires'] == ''
    expiry = int(session.value.split('.')[0])
    assert now+main.SESSION_TTL_SECONDS <= expiry <= int(time.time())+main.SESSION_TTL_SECONDS
    assert response.json()['remember'] is False


def test_persistent_session_server_rejects_expiry_and_key_rotation(web_client, monkeypatch):
    import app.main as main
    client, token = web_client
    response = client.post('/api/session',json={'token':token,'remember':True})
    value = cookie(response).value
    main.guard(None,value)
    old = main.settings
    monkeypatch.setattr(main,'settings',replace(old,token='another-random-token-over-24-characters'))
    with pytest.raises(HTTPException) as error:
        main.guard(None,value)
    assert error.value.status_code == 401
    monkeypatch.setattr(main,'settings',old)
    monkeypatch.setattr(main.time,'time',lambda: int(value.split('.')[0])+1)
    with pytest.raises(HTTPException) as error:
        main.guard(None,value)
    assert error.value.status_code == 401


def test_logout_clears_remembered_cookie_and_https_cookie_is_secure(web_client):
    client, token = web_client
    response = client.post('https://testserver/api/session',json={'token':token,'remember':True})
    assert cookie(response)['secure']
    assert client.get('https://testserver/api/settings').status_code == 200
    response = client.delete('https://testserver/api/session')
    assert cookie(response)['max-age'] == '0'
    assert client.get('https://testserver/api/settings').status_code == 401


def test_remember_value_requires_boolean(web_client):
    client, token = web_client
    response = client.post('/api/session',json={'token':token,'remember':'true'})
    assert response.status_code == 422 and token not in response.text
