"""Shared SEC identities, disclosure types and configuration validation."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import TypedDict
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from .instruments import normalize_symbol, resolve_instrument


def normalize_cik(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,10}',value.strip()) or int(value)==0:
        raise ValueError('CIK 必须是 1–10 位数字')
    return str(int(value))


def sec_symbol(value: str) -> str:
    symbol=normalize_symbol(value)
    if not symbol or resolve_instrument(symbol).market!='US' or symbol.startswith('^'):
        raise ValueError('SEC 映射须使用美股内部代码')
    return symbol


def decimal_text(value, *, nullable=False, non_negative=False) -> str | None:
    if value is None and nullable:
        return None
    raw=str(value).strip()
    if len(raw)>100 or not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?',raw):
        raise ValueError('无效披露数值')
    try:
        number=Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError('无效披露数值') from exc
    if not number.is_finite() or (non_negative and number<0):
        raise ValueError('无效披露数值')
    return format(number,'f')


def iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
        raise ValueError('无效披露日期')
    return date.fromisoformat(value).isoformat()


def validate_ref(ref: dict) -> None:
    cik=normalize_cik(ref['cik']);accession=ref['accession']
    if not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}',accession):
        raise ValueError('无效申报 accession')
    if ref['form'] not in ('13F-HR','13F-HR/A','4','4/A'):
        raise ValueError('不支持的 SEC 申报')
    iso_date(ref['filed_at']);iso_date(ref['report_period'])
    url=urlsplit(ref['source_url'])
    prefix=f'/Archives/edgar/data/{cik}/{accession.replace("-","")}/'
    if url.scheme!='https' or url.netloc!='www.sec.gov' or not url.path.startswith(prefix) or url.query or url.fragment or '..' in url.path or '%' in url.path:
        raise ValueError('无效 SEC 原件来源')


class InstitutionConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=150)
    cik: str
    enabled: StrictBool=True

    @field_validator('cik')
    @classmethod
    def cik_valid(cls,value):
        return normalize_cik(value)

    @field_validator('name')
    @classmethod
    def name_valid(cls,value):
        if not value.strip() or any(ord(c)<32 for c in value):
            raise ValueError('机构名称无效')
        return value.strip()


class FilingRef(TypedDict):
    accession: str
    cik: str
    form: str
    filed_at: str
    report_period: str | None
    primary_document: str
    source_url: str


class Filing(FilingRef):
    entity_name: str
    received_at: str
    amendment_type: str
    completeness: str
    detail: str


class Position(TypedDict):
    row_key: str
    issuer_name: str
    security_class: str
    cusip: str
    shares: str
    value_usd: str
    share_type: str
    put_call: str


class Transaction(TypedDict):
    row_key: str
    issuer_cik: str
    issuer_name: str
    security_title: str
    owners: list[dict]
    derivative: bool
    transaction_date: str | None
    code: str
    acquired_disposed: str
    shares: str | None
    price: str | None
    amount_usd: str | None
    post_shares: str | None
    ownership: str
    ownership_nature: str
    footnotes: dict[str,str]
    ten_b5_1: bool | None


class Progress(TypedDict):
    source_key: str
    initialized: bool
    symbols: list[str]
    pending: list[FilingRef]
    last_success_at: float | None
    last_error: str
    retry_at: float
    failures: int
    coverage_start: str | None
    coverage_end: str | None
    coverage_state: str


class Snapshot(TypedDict):
    cik: str
    report_period: str
    complete: bool
    filings: list[Filing]
    positions: list[Position]
    detail: str


class HoldingsResult(TypedDict):
    items: list[dict]
    period: str | None
    previous_period: str | None
    comparison_state: str
    complete: bool
    filings: list[Filing]
    detail: str


class SourceStatus(TypedDict):
    source_key: str
    name: str
    enabled: bool
    initialized: bool
    symbols: list[str]
    state: str
    pending_count: int
    last_success_at: float | None
    last_error: str
    retry_at: float
    failures: int
    coverage_start: str | None
    coverage_end: str | None
    coverage_state: str


def empty_progress(source_key: str) -> Progress:
    return dict(source_key=source_key,initialized=False,symbols=[],pending=[],last_success_at=None,
        last_error='',retry_at=0,failures=0,coverage_start=None,coverage_end=None,coverage_state='pending')
