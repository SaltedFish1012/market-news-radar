"""Canonical instrument identity shared by prices, research and news."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

MARKETS = {'US': ('USD', 'America/New_York'), 'KR': ('KRW', 'Asia/Seoul'),
           'JP': ('JPY', 'Asia/Tokyo'), 'CN': ('CNY', 'Asia/Shanghai')}
INDEX_MARKETS = {'^N225':'JP', '^GSPC':'US', '^IXIC':'US', '^DJI':'US'}
SESSIONS = ('pre', 'regular', 'post', 'overnight')


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    market: str
    asset_type: str
    currency: str
    timezone: str
    provider_symbols: dict[str, str]


def normalize_symbol(raw: str) -> str:
    if not isinstance(raw, str):
        return ''
    value = raw.strip().upper()
    if value.endswith('.US.US'):
        return ''
    value = value.removesuffix('.US')
    if (re.fullmatch(r'\d{6}\.(KS|KQ|SS|SZ)', value)
            or re.fullmatch(r'\d{4}\.T', value)
            or re.fullmatch(r'\^[A-Z][A-Z0-9]{0,13}', value)
            or (len(value) <= 10 and re.fullmatch(r'[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?', value)
                and not re.search(r'\.[A-Z]{2,}$', value))):
        return value
    return ''


def resolve_instrument(symbol: str, configured: list[dict] | None = None) -> Instrument:
    symbol = normalize_symbol(symbol)
    if not symbol:
        raise ValueError('无效标的代码')
    records = [r for r in (configured or []) if normalize_symbol(r.get('symbol', '')) == symbol]
    if len(records) > 1:
        raise ValueError('标的元数据重复')
    record = records[0] if records else {}
    if set(record) - set(Instrument.__dataclass_fields__):
        raise ValueError('未知标的元数据字段')
    inferred = INDEX_MARKETS.get(symbol) if symbol.startswith('^') else (
        'KR' if symbol.endswith(('.KS','.KQ')) else 'JP' if symbol.endswith('.T') else
        'CN' if symbol.endswith(('.SS','.SZ')) else 'US')
    market = record.get('market') or inferred
    if market not in MARKETS or (inferred and market != inferred):
        raise ValueError('标的市场与代码不一致或无法识别，请配置市场')
    asset_type = record.get('asset_type') or ('index' if symbol.startswith('^') else
        'etf' if symbol in ('SPY','QQQ','SOXX') else 'stock')
    if asset_type not in ('stock','etf','index') or (symbol.startswith('^') and asset_type != 'index'):
        raise ValueError('标的类型与代码不一致')
    currency, timezone = MARKETS[market]
    if record.get('currency', currency) != currency or record.get('timezone', timezone) != timezone:
        raise ValueError('标的币种或时区与市场不一致')
    name = record.get('name') or symbol
    if not isinstance(name, str) or len(name) > 150 or any(ord(c) < 32 for c in name):
        raise ValueError('标的名称无效')
    mapping = {'yahoo': symbol}
    if market == 'US' and asset_type != 'index':
        mapping.update(longbridge=symbol+'.US', alpaca=symbol)
    supplied = record.get('provider_symbols', {})
    if not isinstance(supplied, dict):
        raise ValueError('供应商代码映射无效')
    for provider, code in supplied.items():
        if provider not in ('yahoo','longbridge','alpaca') or not normalize_symbol(code):
            raise ValueError('供应商代码映射无效')
        if provider != 'yahoo' and (market != 'US' or asset_type == 'index'):
            raise ValueError('该市场或指数不支持此供应商映射')
        if provider == 'longbridge' and not code.upper().endswith('.US'):
            raise ValueError('长桥美股映射须使用 .US 后缀')
        mapping[provider] = code.strip().upper()
    return Instrument(symbol, name.strip(), market, asset_type, currency, timezone, mapping)


def normalize_instruments(symbols: list[str], records: list[dict]) -> list[dict]:
    if not isinstance(records, list) or len(records) > 20:
        raise ValueError('标的元数据最多 20 条')
    identities = [normalize_symbol(r.get('symbol', '')) for r in records if isinstance(r, dict)]
    if len(identities) != len(records) or '' in identities or len(set(identities)) != len(identities):
        raise ValueError('标的元数据代码无效或重复')
    if set(identities) - set(symbols):
        raise ValueError('元数据标的需要先加入自选列表')
    return [asdict(resolve_instrument(s, records)) for s in symbols]
