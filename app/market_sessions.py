"""Session-labelled quote facts; receive time is never exchange time."""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import asdict, dataclass
from .instruments import SESSIONS, Instrument

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


def trading_periods(periods: dict | None) -> list[dict]:
    out = []
    def visit(value, session):
        if isinstance(value, list):
            for item in value:
                visit(item, session)
        elif isinstance(value, dict):
            start, end = value.get('start'), value.get('end')
            if (isinstance(start,(int,float)) and isinstance(end,(int,float))
                    and math.isfinite(start) and math.isfinite(end) and end > start):
                out.append({'session':session,'start':start,'end':end})
            else:
                for label in SESSIONS:
                    if label in value:
                        visit(value[label], label)
                for key in ('tradingPeriods','currentTradingPeriod'):
                    if key in value:
                        visit(value[key], session)
    visit(periods, 'regular')
    return out


def classify_bar_session(timestamp: float, periods: dict | None) -> str:
    return next((p['session'] for p in trading_periods(periods) if p['start'] <= timestamp < p['end']), 'unknown')


def bar_closed_at(timestamp: float, interval: str, instrument: Instrument, periods: dict | None) -> float | None:
    step = {'5m':300,'1h':3600}.get(interval)
    if step:
        end = next((p['end'] for p in trading_periods(periods) if p['start'] <= timestamp < p['end']), None)
        return min(timestamp+step,end) if end else timestamp+step
    zone = ZoneInfo(instrument.timezone)
    date = datetime.fromtimestamp(timestamp,zone)
    relevant = [p for p in trading_periods(periods) if p['session'] == 'regular' and
        (datetime.fromtimestamp(p['start'],zone).date() == date.date() if interval == '1d' else
         datetime.fromtimestamp(p['start'],zone).isocalendar()[:2] == date.isocalendar()[:2])]
    if interval == '1wk':
        relevant = [p for p in relevant if datetime.fromtimestamp(p['start'],zone).weekday() == 4]
    return max((p['end'] for p in relevant), default=None)


def bar_is_complete(timestamp: float, interval: str, instrument: Instrument, periods: dict | None, now: float) -> bool:
    closed = bar_closed_at(timestamp,interval,instrument,periods)
    if closed is not None:
        return closed <= now
    zone = ZoneInfo(instrument.timezone)
    date, current = datetime.fromtimestamp(timestamp,zone), datetime.fromtimestamp(now,zone)
    if interval == '1d':
        return date.date() < current.date()
    if interval == '1wk':
        return date.isocalendar()[:2] < current.isocalendar()[:2]
    return False
