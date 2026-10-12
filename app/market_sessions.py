"""Session-labelled quote facts; receive time is never exchange time."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from .instruments import SESSIONS

SESSION_NAMES = {'pre':'盘前', 'regular':'盘中', 'post':'盘后', 'overnight':'夜盘', 'unknown':'未知时段', 'all':'全部时段'}


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    currency: str
    source: str
    session: str
    quote_at: float | None
    received_at: float
    trade_status: str


def quote_is_eligible(quote: Quote) -> bool:
    try:
        return (math.isfinite(quote.price) and quote.price > 0 and quote.session in SESSIONS
            and quote.trade_status == 'normal' and quote.quote_at is not None
            and math.isfinite(quote.quote_at) and math.isfinite(quote.received_at)
            and -5 <= quote.received_at - quote.quote_at <= 120)
    except (TypeError, ValueError):
        return False


def quote_view(quote: Quote, now: float) -> dict:
    fresh = (quote_is_eligible(quote) and -5 <= now - quote.quote_at <= 120)
    return {**asdict(quote), 'fresh': fresh,
            'session_name': SESSION_NAMES.get(quote.session, '未知时段'),
            'status': '实时报价' if fresh else '历史报价／已过期' if quote.quote_at is not None else '未知报价时间'}


def reference_close(bars: list[dict], quote_at: float) -> dict | None:
    candidates = [b for b in bars if b.get('session') == 'regular' and b.get('complete')
        and isinstance(b.get('closed_at'), (int, float)) and b['closed_at'] <= quote_at
        and math.isfinite(b.get('close', float('nan'))) and b['close'] > 0]
    if not candidates:
        return None
    bar = max(candidates, key=lambda b: b['closed_at'])
    return {'price':bar['close'], 'closed_at':bar['closed_at']}
