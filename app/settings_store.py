"""Validated, atomically persisted local settings; public snapshots never reveal keys."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .signals import norm_symbol
from .smart_money_models import InstitutionConfig, normalize_cik, sec_symbol
from .instruments import normalize_instruments, SESSIONS

PROVIDERS = {
    'openai': {'name': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'protocol': 'responses',
               'models': ['gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-astra'],
               'docs': 'https://developers.openai.com/api/docs/models'},
    'gemini': {'name': 'Google Gemini', 'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'protocol': 'gemini',
               'models': ['gemini-3.8-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-pro-preview', 'gemini-2.5-flash', 'gemini-2.5-pro'],
               'docs': 'https://ai.google.dev/gemini-api/docs/models'},
    'anthropic': {'name': 'Anthropic Claude', 'base_url': 'https://api.anthropic.com/v1', 'protocol': 'messages',
                  'models': ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5'],
                  'docs': 'https://platform.claude.com/docs/en/models/overview'},
    'compatible': {'name': 'OpenAI 兼容接口', 'base_url': 'http://127.0.0.1:11434/v1', 'protocol': 'chat_completions',
                   'models': [], 'docs': 'https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create'},
}
SECRETS = ('ai_key', 'alpaca_key', 'alpaca_secret', 'telegram_token', 'bark_key',
           'lb_app_key', 'lb_app_secret', 'lb_access_token', 'x_bearer_token')


def checked_url(value: str, websocket=False, allow_query=False) -> str:
    value = value.strip() if allow_query else value.strip().rstrip('/')
    url = urlsplit(value)
    local = url.hostname in ('127.0.0.1', 'localhost', '::1')
    if not url.hostname or url.username or url.password or (url.query and not allow_query) or url.fragment or any(c.isspace() for c in value):
        raise ValueError('接口地址必须为完整 URL，不能包含账号、密码、查询参数或空格')
    if websocket:
        if url.scheme != 'wss':
            raise ValueError('新闻 WebSocket 地址必须使用 wss://')
    elif url.scheme != 'https' and not (url.scheme == 'http' and local):
        raise ValueError('接口地址必须使用 HTTPS；本机服务允许 HTTP')
    return value


class XAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    enabled: StrictBool = True
    push_mode: Literal['all','filtered'] = 'filtered'
    include_replies: StrictBool = False
    include_reposts: StrictBool = False
    keywords: list[str] = Field(default_factory=list,max_length=50)
    related_symbols: list[str] = Field(default_factory=list,max_length=20)

    @field_validator('username')
    @classmethod
    def username_valid(cls,value):
        value = value.strip().removeprefix('@')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',value):
            raise ValueError('X 用户名须为 1–15 位 ASCII 字母、数字或下划线')
        return value

    @field_validator('keywords')
    @classmethod
    def keywords_valid(cls,values):
        if any(not v.strip() or len(v)>100 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError('X 关键词须为 1–100 字符且不含控制字符')
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator('related_symbols')
    @classmethod
    def symbols_valid(cls,values):
        out = list(dict.fromkeys(norm_symbol(v) for v in values))
        if '' in out: raise ValueError('X 绑定代码无效')
        return out


class Values(BaseModel):
    model_config = ConfigDict(extra='forbid')
    smart_money_enabled: StrictBool | None = None
    smart_money_poll_interval: int | None = Field(None,ge=300,le=86400,strict=True)
    smart_money_institutions: list[InstitutionConfig] | None = Field(None,max_length=20)
    smart_money_cusip_map: dict[str,str] | None = None
    smart_money_issuer_map: dict[str,str] | None = None

    @field_validator('smart_money_institutions')
    @classmethod
    def distinct_institutions(cls,values):
        ids=[v.cik for v in (values or [])]
        if len(ids)!=len(set(ids)):raise ValueError('机构 CIK 不能重复')
        return values

    @field_validator('smart_money_cusip_map')
    @classmethod
    def cusip_map_valid(cls,values):
        if values is None:return None
        if len(values)>200:raise ValueError('CUSIP 映射最多 200 条')
        result={}
        for cusip,symbol in values.items():
            key=cusip.strip().upper()
            if not re.fullmatch(r'[A-Z0-9*@#]{9}',key) or key in result:raise ValueError('CUSIP 无效或重复')
            result[key]=sec_symbol(symbol)
        return result

    @field_validator('smart_money_issuer_map')
    @classmethod
    def issuer_map_valid(cls,values):
        if values is None:return None
        if len(values)>20:raise ValueError('发行人映射最多 20 条')
        result={}
        for symbol,cik in values.items():
            key=sec_symbol(symbol)
            if key in result:raise ValueError('发行人代码重复')
            result[key]=normalize_cik(cik)
        return result

    x_enabled: StrictBool | None = None
    x_poll_interval: int | None = Field(None,ge=60,le=3600)
    x_accounts: list[XAccount] | None = Field(None,max_length=20)

    @field_validator('x_accounts')
    @classmethod
    def distinct_accounts(cls,values):
        names = [a.username.casefold() for a in (values or [])]
        if len(set(names)) != len(names): raise ValueError('X 账号不能重复')
        return values

    ai_enabled: StrictBool | None = None
    ai_provider: Literal['openai', 'gemini', 'anthropic', 'compatible'] | None = None
    ai_model: str | None = Field(None, min_length=1, max_length=150, pattern=r'^[A-Za-z0-9_.:/-]+$')
    ai_base_url: str | None = Field(None, max_length=500)
    ai_max_requests_per_hour: int | None = Field(None, ge=1, le=600)
    ai_timeout_seconds: int | None = Field(None, ge=10, le=120)
    market_provider: Literal['yahoo', 'alpaca', 'demo', 'longbridge'] | None = None
    alpaca_news_enabled: StrictBool | None = None
    alpaca_url: str | None = Field(None, max_length=500)
    lb_enabled: StrictBool | None = None
    lb_news_enabled: StrictBool | None = None
    lb_overnight_enabled: StrictBool | None = None
    lb_client_id: str | None = Field(None, max_length=200)
    notifications_enabled: StrictBool | None = None
    telegram_chat: str | None = Field(None, max_length=120)
    bark_base: str | None = Field(None, max_length=500)
    sec_contact: str | None = Field(None, max_length=200)
    rss_interval: int | None = Field(None, ge=30, le=3600)
    lb_news_interval: int | None = Field(None, ge=60, le=3600)
    news_max_age_minutes: int | None = Field(None, ge=1, le=1440)

    @field_validator('ai_base_url', 'bark_base')
    @classmethod
    def url(cls, value):
        return checked_url(value) if value is not None else value

    @field_validator('alpaca_url')
    @classmethod
    def websocket(cls, value):
        return checked_url(value, websocket=True) if value is not None else value

    @field_validator('lb_client_id', 'telegram_chat', 'sec_contact')
    @classmethod
    def plain_text(cls, value):
        if value is not None and any(ord(c) < 32 for c in value):
            raise ValueError('配置文本不能包含控制字符')
        return value.strip() if value is not None else value


class SecretChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ai_key: str = Field('', max_length=4096, repr=False)
    alpaca_key: str = Field('', max_length=4096, repr=False)
    alpaca_secret: str = Field('', max_length=4096, repr=False)
    telegram_token: str = Field('', max_length=4096, repr=False)
    bark_key: str = Field('', max_length=4096, repr=False)
    lb_app_key: str = Field('', max_length=4096, repr=False)
    lb_app_secret: str = Field('', max_length=4096, repr=False)
    lb_access_token: str = Field('', max_length=4096, repr=False)
    x_bearer_token: str = Field('', max_length=4096, repr=False)

    @field_validator('*')
    @classmethod
    def credential(cls, value):
        value = value.strip()
        if value and (not value.isascii() or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError('密钥不能包含空格、非 ASCII 字符或控制字符')
        return value


class PriceRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(max_length=15)
    kind: Literal['price_below', 'price_above', 'change_5m_below_pct', 'change_5m_above_pct']
    value: float = Field(allow_inf_nan=False)
    cooldown_s: int = Field(300, ge=1, le=86400)
    sessions: list[Literal['pre','regular','post','overnight']] = Field(default_factory=lambda: list(SESSIONS), min_length=1, max_length=4)

    @field_validator('sessions')
    @classmethod
    def unique_sessions(cls, values):
        return list(dict.fromkeys(values))

    @field_validator('symbol')
    @classmethod
    def symbol_code(cls, value):
        symbol = norm_symbol(value)
        if not symbol:
            raise ValueError('无效股票代码')
        return symbol


class Watchlist(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] = Field(min_length=1, max_length=20)
    instruments: list[dict] = Field(default_factory=list, max_length=20)
    theme_keywords: list[str] = Field(default_factory=list, max_length=100)
    company_map: dict[str, list[str]] = Field(default_factory=dict)
    price_rules: list[PriceRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def metadata(self):
        self.instruments = normalize_instruments(self.symbols, self.instruments)
        return self

    @field_validator('symbols')
    @classmethod
    def symbols_valid(cls, values):
        normalized = list(dict.fromkeys(norm_symbol(v) for v in values))
        if '' in normalized:
            raise ValueError('自选列表含无效股票代码')
        return normalized

    @field_validator('theme_keywords')
    @classmethod
    def keywords(cls, values):
        if any(not v.strip() or len(v) > 100 for v in values):
            raise ValueError('主题关键词须为 1–100 个字符')
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator('company_map')
    @classmethod
    def relationships(cls, values):
        if len(values) > 100:
            raise ValueError('公司映射最多 100 条')
        out = {}
        for company, symbols in values.items():
            if not company.strip() or len(company) > 100 or len(symbols) > 20:
                raise ValueError('公司映射名称或关联股票数量不符合要求')
            group = [norm_symbol(s) for s in symbols]
            if '' in group:
                raise ValueError('公司映射含无效股票代码')
            out[company.strip()] = list(dict.fromkeys(group))
        return out


class Feed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=1500)
    enabled: StrictBool = True
    priority: str = Field('secondary', max_length=30)
    needs_sec_contact: StrictBool = False

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('新闻源名称不能为空或包含控制字符')
        return value

    @field_validator('url')
    @classmethod
    def url_valid(cls, value):
        return checked_url(value, allow_query=True)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    values: Values = Field(default_factory=Values)
    secrets: SecretChanges = Field(default_factory=SecretChanges, repr=False)
    clear_secrets: list[Literal['ai_key', 'alpaca_key', 'alpaca_secret', 'telegram_token', 'bark_key',
                               'lb_app_key', 'lb_app_secret', 'lb_access_token', 'x_bearer_token']] = Field(default_factory=list)
    watchlist: Watchlist | None = None
    feeds: list[Feed] | None = Field(None, max_length=30)


class ConfigConflict(Exception):
    pass


class SettingsStore:
    def __init__(self, base, path=None):
        self.base = base
        self.path = Path(path) if path else Path(base.db_path).parent / 'settings.json'
        provider = base.ai_provider if base.ai_provider in PROVIDERS else 'compatible'
        self.state = {'revision': 0, 'values': {name: getattr(base, name) for name in Values.model_fields},
                      'secrets': {name: getattr(base, name) for name in SECRETS if name != 'ai_key'},
                      'ai_profiles': {p: {'model': info['models'][0] if info['models'] else 'your-model-id',
                                         'base_url': info['base_url'], 'key': ''} for p, info in PROVIDERS.items()},
                      'watchlist': copy.deepcopy(base.read_json('watchlist.json')),
                      'feeds': copy.deepcopy(base.read_json('feeds.json'))}
        self.state['values']['ai_provider'] = provider
        self.state['ai_profiles'][provider] = {'model': base.ai_model, 'base_url': base.ai_base_url, 'key': base.ai_key}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.state = self._validate_state(saved)
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError('本地设置文件无效，请修复 data/settings.json 后重启') from exc
        else:
            self.state = self._validate_state(self.state)

    def _validate_state(self, data):
        state = copy.deepcopy(data)
        values = Values.model_validate(state['values']).model_dump(exclude_none=True)
        state['values'] = {**self.state['values'], **values}
        if not isinstance(state['revision'], int) or state['revision'] < 0:
            raise ValueError('配置版本无效')
        if set(state['ai_profiles']) != set(PROVIDERS):
            raise ValueError('供应商配置无效')
        for profile in state['ai_profiles'].values():
            profile['base_url'] = checked_url(profile['base_url'])
            profile['model'] = Values(ai_model=profile['model']).ai_model
            profile['key'] = SecretChanges(ai_key=profile['key']).ai_key
        state['secrets'] = SecretChanges.model_validate(state['secrets']).model_dump(exclude={'ai_key'})
        state['watchlist'] = Watchlist.model_validate(state['watchlist']).model_dump()
        state['feeds'] = [Feed.model_validate(f).model_dump() for f in state['feeds']]
        names = [f['name'] for f in state['feeds']]
        if len(names) != len(set(names)):
            raise ValueError('新闻源名称不能重复')
        for rule in state['watchlist']['price_rules']:
            if rule['kind'].startswith('price_') and rule['value'] <= 0:
                raise ValueError('价格阈值必须大于 0')
            if rule['symbol'] not in state['watchlist']['symbols']:
                raise ValueError('价格规则的股票需要先加入自选列表')
        provider = state['values']['ai_provider']
        profile = state['ai_profiles'][provider]
        if provider == 'gemini' and not re.fullmatch(r'(models/)?[A-Za-z0-9._-]+', profile['model']):
            raise ValueError('Gemini 模型名称格式无效')
        state['values'].update(ai_model=profile['model'], ai_base_url=profile['base_url'])
        if state['values']['ai_enabled'] and not profile['key']:
            raise ValueError('启用 AI 分析前请配置该供应商的 API Key')
        secrets = state['secrets']
        if state['values']['x_enabled'] and (not secrets['x_bearer_token'] or not any(a['enabled'] for a in state['values']['x_accounts'])):
            raise ValueError('启用 X 需要 Bearer Token 和至少一个启用账号')
        if state['values']['alpaca_news_enabled'] and not (secrets['alpaca_key'] and secrets['alpaca_secret']):
            raise ValueError('启用 Alpaca 新闻需要 API Key 和 Secret')
        if state['values']['notifications_enabled'] and bool(secrets['telegram_token']) != bool(state['values']['telegram_chat']):
            raise ValueError('Telegram Bot Token 和 Chat ID 需要同时配置，或先关闭通知')
        if state['values']['lb_enabled'] or state['values']['lb_news_enabled']:
            if not state['values']['lb_client_id'] and not all(secrets[k] for k in ('lb_app_key','lb_app_secret','lb_access_token')):
                raise ValueError('启用长桥前请设置 OAuth Client ID 或完整的 API 认证')
        return state

    def prepare(self, update):
        if update.revision != self.state['revision']:
            raise ConfigConflict('设置已被其他页面修改，请重新载入后再保存')
        candidate = copy.deepcopy(self.state)
        changes = update.values.model_dump(exclude_unset=True, exclude_none=True)
        candidate['values'].update(changes)
        provider = candidate['values']['ai_provider']
        profile = candidate['ai_profiles'][provider]
        for key, field in (('model','ai_model'), ('base_url','ai_base_url')):
            if field in changes:
                profile[key] = changes[field]
        for name, value in update.secrets.model_dump().items():
            if value:
                if name == 'ai_key':
                    profile['key'] = value
                else:
                    candidate['secrets'][name] = value
        for name in update.clear_secrets:
            if getattr(update.secrets, name):
                raise ValueError('不能同时替换和清除同一个密钥')
            if name == 'ai_key':
                profile['key'] = ''
            else:
                candidate['secrets'][name] = ''
        if update.watchlist is not None:
            candidate['watchlist'] = update.watchlist.model_dump()
        if update.feeds is not None:
            candidate['feeds'] = [f.model_dump() for f in update.feeds]
        candidate['revision'] += 1
        return self._validate_state(candidate)

    def settings(self, data=None):
        data = data or self.state
        provider = data['values']['ai_provider']
        return replace(self.base, **data['values'], **data['secrets'],
                       ai_key=data['ai_profiles'][provider]['key'],
                       watchlist_override=copy.deepcopy(data['watchlist']), feeds_override=copy.deepcopy(data['feeds']))

    def commit(self, candidate):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix='.settings-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(candidate, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
            self.state = candidate
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def snapshot(self):
        provider = self.state['values']['ai_provider']
        return {'revision': self.state['revision'], 'values': copy.deepcopy(self.state['values']),
                'secret_status': {**{k: bool(v) for k,v in self.state['secrets'].items()},
                                  'ai_key': bool(self.state['ai_profiles'][provider]['key'])},
                'ai_profiles': {p: {'model': profile['model'], 'base_url': profile['base_url'], 'key_set': bool(profile['key'])}
                                for p, profile in self.state['ai_profiles'].items()},
                'providers': copy.deepcopy(PROVIDERS), 'watchlist': copy.deepcopy(self.state['watchlist']),
                'feeds': copy.deepcopy(self.state['feeds']), 'saved': self.path.exists()}

    def redact(self, value):
        text = str(value)
        keys = list(self.state['secrets'].values()) + [p['key'] for p in self.state['ai_profiles'].values()]
        for key in sorted(filter(None, keys), key=len, reverse=True):
            text = text.replace(key, '[REDACTED]')
        return text
