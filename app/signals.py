from __future__ import annotations
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib, re, time, math
from .instruments import normalize_symbol, SESSIONS

MATERIAL = (
    'misses estimates','beats estimates','lowered guidance','cuts guidance','raises guidance',
    'revenue','earnings','guidance','bankruptcy','chapter 11','default','fraud',
    'acquisition','merger','buyout','takeover','data breach','shutdown','contract cancelled',
    'tariff','sanctions','fomc','interest rate','rate cut','rate hike','fed chair',
    'inflation','nonfarm payrolls','cpi','sec filing','form 8-k','8-k','downgrade',
    'fda approval','halted','investigation','job cuts','layoffs','profit warning',
    'capex','capital expenditures','weak demand','debt','liquidity',
)
MAJOR = ('bankruptcy','default','fraud','halted','fomc','interest rate','rate hike','rate cut','misses estimates','profit warning','guidance','takeover','acquisition','investigation')


def norm_symbol(raw: str) -> str:
    return normalize_symbol(raw)


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.casefold()
    return [w for w in keywords if w.casefold() in lowered]


def related_symbols(title: str, summary: str, provider_symbols: list[str], cfg: dict) -> tuple[list[str], list[str]]:
    raw = title + ' ' + summary
    tickers = {norm_symbol(v) for v in provider_symbols}
    tickers.discard('')
    symbols = set(tickers)
    themes = matched_keywords(raw, cfg.get('theme_keywords', []))
    lower = raw.lower()
    for company, group in cfg.get('company_map', {}).items():
        if company.lower() in lower:
            # Mapping means possible thematic exposure, never proof of causal impact.
            symbols.update(filter(None, map(norm_symbol, group)))
    return sorted(symbols), themes


def classify(title: str, summary: str, provider_symbols: list[str], cfg: dict, source: str = '') -> tuple[str,list[str],list[str]]:
    symbols, themes = related_symbols(title, summary, provider_symbols, cfg)
    haystack = (title + ' ' + summary).lower()
    material = any(w in haystack for w in MATERIAL)
    severe = any(w in haystack for w in MAJOR)
    watch = set(map(norm_symbol, cfg.get('symbols', [])))
    direct = bool(set(map(norm_symbol, provider_symbols)) & watch)
    focus = bool(themes or (watch & set(symbols)))
    if (severe and (focus or bool(provider_symbols))) or (source == 'SEC EDGAR Recent Filings' and focus and material):
        return 'critical', symbols, themes
    if material and (focus or direct):
        return 'high', symbols, themes
    return 'info', symbols, themes


def event_hash(title: str, published_at: str | None, external_id: str = '') -> str:
    if external_id:
        # Id is namespaced by caller, avoids Python hash randomization.
        return hashlib.sha256(external_id.encode()).hexdigest()
    normalized = re.sub(r'\W+', ' ', title.lower()).strip()
    day = (published_at or datetime.now(timezone.utc).date().isoformat())[:10]
    return hashlib.sha256((normalized+'|'+day).encode()).hexdigest()


class PriceRules:
    def __init__(self, cfg: dict):
        self.rules = cfg.get('price_rules', [])
        self.history = defaultdict(lambda: deque(maxlen=2500))
        self.last_price = {}
        self.last_fired = {}
        self.change_state = {}

    def feed(self, symbol: str, price: float, now: float | None = None, *, session: str = 'unknown') -> list[dict]:
        now = now if now is not None else time.time()
        symbol = norm_symbol(symbol)
        if not symbol or session not in SESSIONS or not math.isfinite(price) or price <= 0 or not math.isfinite(now):
            return []
        identity = (symbol, session)
        series = self.history[identity]
        if series and now <= series[-1][0]:
            return []
        prefix = f'{symbol}|{session}|'
        if series and now - series[-1][0] > 370:
            series.clear()
            self.last_price.pop(identity, None)
            for key in list(self.change_state):
                if key.startswith(prefix):
                    self.change_state.pop(key)
        previous = self.last_price.get(identity)
        self.last_price[identity] = price
        series.append((now, price))
        while series and series[0][0] < now-370:
            series.popleft()
        alerts = []
        for index, rule in enumerate(self.rules):
            if norm_symbol(rule.get('symbol','')) != symbol or session not in rule.get('sessions', SESSIONS):
                continue
            kind, level = rule.get('kind',''), float(rule.get('value', 0))
            key = f'{prefix}{kind}|{level}|{index}'
            cooldown = int(rule.get('cooldown_s',300))
            passed, detail = False, ''
            if kind == 'price_below':
                passed = previous is not None and previous >= level and price < level
                detail = f'{symbol} 跌破 {level:g}，最新 {price:g}'
            elif kind == 'price_above':
                passed = previous is not None and previous <= level and price > level
                detail = f'{symbol} 突破 {level:g}，最新 {price:g}'
            elif kind in ('change_5m_below_pct','change_5m_above_pct'):
                base = next((p for t,p in series if t <= now-240), None)
                if base:
                    pct = (price/base-1)*100
                    active = pct <= level if kind == 'change_5m_below_pct' else pct >= level
                    passed = active and not self.change_state.get(key,False)
                    self.change_state[key] = active
                    detail = f'{symbol} 约5分钟涨跌幅 {pct:+.2f}%（阈值 {level:+g}%），最新 {price:g}'
            if passed and now-self.last_fired.get(key,float('-inf')) >= cooldown:
                self.last_fired[key] = now
                alerts.append({'key':key,'detail':detail,'symbol':symbol,'price':price,
                               'session':session,'cooldown_s':cooldown,'quote_at':now})
        return alerts
