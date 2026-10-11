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
