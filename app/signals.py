from __future__ import annotations
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib, re, time
from .instruments import normalize_symbol

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
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=2500))
        self.last_price: dict[str, float] = {}
        self.last_fired: dict[str, float] = {}
        self.change_state: dict[str, bool] = {}

    def feed(self, symbol: str, price: float, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        symbol = norm_symbol(symbol)
        if not symbol or price <= 0:
            return []
        series=self.history[symbol]
        series.append((now,price))
        while series and series[0][0] < now-370:
            series.popleft()
        previous=self.last_price.get(symbol)
        self.last_price[symbol]=price
        alerts=[]
        for rule in self.rules:
            if norm_symbol(rule.get('symbol',''))!=symbol:continue
            kind=rule.get('kind','')
            level=float(rule.get('value', 0))
            key=f'{symbol}|{kind}|{level}'
            cooldown=int(rule.get('cooldown_s',300))
            passed=False
            detail=''
            if kind=='price_below':
                passed=previous is not None and previous >= level and price < level
                detail=f'{symbol} 跌破 {level:g}，最新 {price:g}'
            elif kind=='price_above':
                passed=previous is not None and previous <= level and price > level
                detail=f'{symbol} 突破 {level:g}，最新 {price:g}'
            elif kind=='change_5m_below_pct':
                base=next((p for t,p in series if t <= now-240),None)
                if base and base>0:
                    pct=(price/base-1)*100
                    active=pct <= level
                    passed=active and not self.change_state.get(key,False)
                    self.change_state[key]=active
                    detail=f'{symbol} 约5分钟涨跌幅 {pct:+.2f}%（阈值 {level:+g}%），最新 {price:g}'
            elif kind=='change_5m_above_pct':
                base=next((p for t,p in series if t <= now-240),None)
                if base and base>0:
                    pct=(price/base-1)*100
                    active=pct >= level
                    passed=active and not self.change_state.get(key,False)
                    self.change_state[key]=active
                    detail=f'{symbol} 约5分钟涨跌幅 {pct:+.2f}%（阈值 {level:+g}%），最新 {price:g}'
            if passed and now-self.last_fired.get(key,0)>=cooldown:
                self.last_fired[key]=now
                alerts.append({'key':key,'detail':detail,'symbol':symbol,'price':price})
        return alerts
