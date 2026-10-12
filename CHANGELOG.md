# v0.4.0 — market sessions and attributed X speech
- Unified instrument, market, currency, timezone and provider mappings; legacy settings remain readable
- Four-session U.S. quote storage, freshness labels and independent session alert windows
- Optional Longbridge 5.2+ candle adapter, safe session filtering and explicit coverage limits
- Korean, Japanese/Nikkei and Chinese public history and indicators; guarded execution backtests
- X specified-account polling, all/filtered pushes, silent initialization, resumable pages and durable backoff
- Original text, author identity, reference roles, edit dedupe and atomic first-alert storage
- Responsive session and X settings/news/status UI; instrument-aware dates, currency and CSV
- Windows/POSIX test harness and backend/DOM regression coverage; live account acceptance remains separate

# v0.2.0 — local market research workspace
- Responsive news, watchlist, candlestick, volume and indicator dashboard
- Public Yahoo Finance and authenticated Alpaca IEX historical OHLCV adapters
- Explicit synthetic demo mode; no silent substitution when a real source fails
- EMA, RSI, Donchian, MACD and Bollinger long-only rules
- Next-open backtests with per-side fees/slippage, equity, benchmark and trade export
- Signed HttpOnly session login; existing Bearer API authentication retained
- Tests for causal signals/equity, next-open fills, costs, unfinished candles and auth

# v0.1.0 — offline validated MVP
- Alpaca full-news WebSocket ingest, subscription capability probe and reconnect
- RSS/Atom poller (Fed, SEC when contact provided, Google News) with startup warmup
- Watchlist / theme / company potential-exposure map
- Basic deterministic importance rules + optional AI JSON assessment
- Longbridge quote subscription + symbol-news polling (requires actual SDK auth)
- Price crossing and 5-minute fall alerts
- SQLite WAL event store and retry outbox
- Telegram / Bark push, bearer-protected lightweight dashboard, Docker Compose
- Basic unit tests; third-party live connections not verified
