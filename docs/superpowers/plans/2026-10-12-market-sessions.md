# 多市场配置与美股四时段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付美股四时段报价、K 线与价格预警，再接入韩股、日本和 A 股历史行情。

**Architecture:** 以统一标的模型和时段模型贯穿配置、连接器、数据库、API 与页面。长桥提供美股实时数据及分时 K 线，Yahoo 提供其他市场历史数据；公共价格和实时价格分别标明来源。沿用现有指标和新闻管线，限制不具备当地成交规则的回测。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLite、httpx、可选 longbridge SDK、原生 JavaScript/CSS、pytest、Node.js 24.13+、jsdom 29.1.1。

**Spec:** [已确认设计](../specs/2026-10-12-market-sessions-design.md)。状态：实施计划待审阅。

## Global Constraints

- “先补齐美股盘前、盘中、盘后、夜盘的行情展示与价格预警，建立统一的标的、市场、币种和时区配置，然后加入韩股、日本市场及 A 股的历史行情。”
- “统一时段值为 `pre`、`regular`、`post`、`overnight`；无法确认时段的报价为 `unknown`。`all` 仅用于查询与规则选择，不作为真实报价的时段值。”
- “实时预警只接受上游时间距接收时间不超过 120 秒、未来偏差不超过 5 秒的有效报价，本轮阈值固定。”
- “超过 370 秒没有连续数据后重建基准，第一笔不作为跨越阈值的证据。”
- “金额不做跨币种自动换算。”其他市场和美股延长时段本轮不支持成交回测。
- “既有配置不在读取时重写；用户保存设置时持久化新增字段。”保持密钥、revision、旧新闻、预警和报价记录。
- “验证沿用 Python 3.12 后端 pytest 和 Node.js 24.13+ DOM 测试；前端测试的 Python 路径需同时兼容 Windows `.venv/Scripts/python.exe` 与 Unix `.venv/bin/python`。”
- “开发不使用真实凭证发送付费模型请求或手机通知；实盘连接由用户在设置页启用。”

## Review Focus

1. 同一配置里的 `.US` 别名、数字前导零和重复元数据必须合并或拒绝，不能关联到另一市场；由任务 1 测试。
2. SDK 回调已排入事件循环后用户更换配置，旧连接仍不得写入报价或通知；由任务 3 测试。
3. 报价跨过夏令时、午夜或收盘边界时，涨跌幅不得使用报价之后的日线收盘；由任务 2、4、6 测试。
4. 快速切换标的、来源或时段后，晚到响应不得覆盖新的选择，币种和导出数据必须属于当前结果；由任务 7 测试。
5. 没有时段元数据、币种不符或一个市场失败时，页面必须说明限制并继续显示其他标的；由任务 4、6、8 测试。

## 文件与接口约定

新增 `app/instruments.py` 负责标的解析；`app/market_sessions.py` 负责报价及时间判断；`app/longbridge_market.py` 封装可选 SDK 的纯转换和历史请求。现有 `market.py` 保持行情调度和缓存职责，不把 SDK 细节放入 API 层。

修改 `config.py`、`settings_store.py` 管理标的及夜盘；`signals.py`、`engine.py`、`connectors.py`、`db.py` 管理四时段报价和预警；`main.py` 集成查询、热应用及回测限制；`ai_client.py` 扩展标的语义。页面改动集中于 `app/assets/index.html`、`dashboard.js`、`settings.js`、`dashboard.css`，不更换框架。

以下时间值均为 UTC Unix 秒（`float`）；日期展示由 `Instrument.timezone` 决定。SQLite 新表存秒值，旧表保留 ISO 时间字段。`Candle` 是沿用 OHLCV 字典并新增 `session: str`、`closed_at: float | None`、`complete: bool`；不能从固定纽约 16 点伪造 `closed_at`。

新增测试均自行构造临时 Settings/DB 和模拟 HTTP/SDK；API 测试沿用 `tests/test_research.py` 的 `web_client(tmp_path, monkeypatch)` 模式，明确关闭长桥、Alpaca 新闻、X、AI 和真实通知，feeds 使用空列表，不继承本机 `.env` 的外部连接配置。下列 captured/result/client 等变量是该测试准备的模拟调用或响应，不是新增应用接口。

执行测试时使用项目隔离环境；Windows 命令在下文写明，Unix 将 Python 前缀换为 `.venv/bin/python`。先安装 `pytest`；运行依赖增加 `tzdata` 以支持 Windows。DOM 测试依赖可安装于临时目录：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
npm install --prefix "$env:TEMP/radar-dom-tests" jsdom@29.1.1 --no-save --ignore-scripts
$env:NODE_PATH = Join-Path $env:TEMP 'radar-dom-tests/node_modules'
```

## Task 1: 统一标的与兼容配置

**Files:** Create `app/instruments.py`, `tests/test_instruments.py`; Modify `app/config.py`, `app/settings_store.py`, `app/signals.py`, `config/watchlist.json`, `.env.example`, `requirements.txt`, `tests/test_settings.py`。

**Interfaces:**
- Produces frozen `Instrument(symbol: str, name: str, market: str, asset_type: str, currency: str, timezone: str, provider_symbols: dict[str, str])`、`normalize_symbol(raw: str) -> str`（无效返回空字符串）、`resolve_instrument(symbol: str, configured: list[dict] | None = None) -> Instrument`（无法解析抛 `ValueError`）、`normalize_instruments(symbols: list[str], records: list[dict]) -> list[dict]`。
- `Watchlist.instruments` 保存元数据列表；`PriceRule.sessions` 默认 `['pre','regular','post','overnight']`。`Settings.lb_overnight_enabled` 从 `LONGBRIDGE_ENABLE_OVERNIGHT` 初始化。`signals.norm_symbol` 保持现有名称并委托统一解析。
- US/KR/JP/CN 默认币种与时区遵循设计表；映射仅生成该市场支持的供应商代码。元数据可编辑，但 market/currency/timezone 必须一致；未自选的常见后缀仍可解析。美股指数不能生成股票长桥映射。

- [x] **Step 1: 添加失败测试 `test_symbol_aliases_and_leading_zeros`、`test_metadata_conflicts`、`test_old_settings_migrate_only_in_memory`。** 断言如下；配置测试另断言旧文件字节、密钥、AI profiles、feeds、revision 不变，保存后才包含元数据和 sessions。

```python
assert normalize_symbol(' nvda.US ') == 'NVDA'
assert resolve_instrument('005930.KS').currency == 'KRW'
assert resolve_instrument('^N225').asset_type == 'index'
assert normalize_symbol('../NVDA') == ''
# 相同内部代码存在矛盾元数据、未知市场代码、指数声明为 stock 均抛 ValueError。
```

- [x] **Step 2: 运行失败验证。** ` .\.venv\Scripts\python.exe -m pytest tests/test_instruments.py tests/test_settings.py -q`；新增测试因缺少模型或字段失败。
- [x] **Step 3: 实现上述接口与配置迁移。** 按结构验证 US ticker、6 位 `.KS/.KQ/.SS/.SZ`、4 位 `.T` 和已知指数；保留前导零，provider 路径仍在请求时编码。拒绝空 sessions、未知 session、元数据与自选矛盾；兼容旧规则字段。补齐旧字段时不重写磁盘，保持原子保存与冲突响应；新字段进入 snapshot 和运行配置。增加 `tzdata`，示例自选仍保持现有美股。
- [x] **Step 4: 重跑同一命令。** 预期全部 PASS，包括 revision 冲突、保存失败和密钥隐藏的原测试。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: unify instrument metadata and session settings"`。

## Task 2: 报价模型、鲜度和数据库迁移

**Files:** Create `app/market_sessions.py`, `tests/test_market_sessions.py`, `tests/test_quote_storage.py`; Modify `app/db.py`。

**Interfaces:**
- Consumes `Instrument`。
- Produces frozen `Quote(symbol: str, price: float, currency: str, source: str, session: str, quote_at: float | None, received_at: float, trade_status: str)`；`quote_is_eligible(quote: Quote) -> bool`；`quote_view(quote: Quote, now: float) -> dict`（含 `fresh` 和状态原因）；`reference_close(bars: list[dict], quote_at: float) -> dict | None`（返回 `price`、`closed_at`）。
- `DB.put_session_quote(quote: Quote) -> bool` 原子拒绝同一 symbol/session 的重复或倒序时间；`DB.session_quotes(symbol: str | None = None) -> list[dict]` 返回完整字段。`DB.list_rows('quotes')` 和旧 `DB.quote` 保留兼容读取；未知时间旧数据不能触发预警。

- [x] **Step 1: 添加失败测试 `test_quote_freshness_boundaries`、`test_reference_close_has_no_future_data`、`test_repeatable_quote_migration_and_order`。** 使用固定时钟并断言：

```python
assert quote_is_eligible(Quote('NVDA', 100, 'USD', 'Longbridge OpenAPI', 'pre', 1000, 1120, 'normal'))
assert not quote_is_eligible(Quote('NVDA', 100, 'USD', 'Longbridge OpenAPI', 'pre', 1000, 1120.01, 'normal'))
assert not quote_is_eligible(Quote('NVDA', 100, 'USD', 'Longbridge OpenAPI', 'pre', 1006, 1000, 'normal'))
assert reference_close([{'session':'regular', 'complete':True, 'close':110, 'closed_at':1100}], 1000) is None
```

另外覆盖 `NaN`、无穷、负价、unknown、交易停牌；未来正好 5 秒可用；同秒修订不刷新预警；120 秒之后读取已过期；打开旧数据库两次保留新闻/预警/旧价且未知记录仅一份。

- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_market_sessions.py tests/test_quote_storage.py -q`；新增模型/方法未定义而失败。
- [x] **Step 3: 实现模型、边界判定和迁移。** 新增 `session_quotes`，主键 `(symbol, session)`；用条件 UPSERT 保证时间顺序。成功存储后，同一事务维护 `quotes` 兼容最新行，不能用较旧的另一时段快照覆盖较新的已知上游时间；兼容行的 observed_at 继续为 ISO 接收时间。迁移旧 quote 为 `unknown`，以原接收时间保留显示；没有上游时间保持 null。实时状态按读取时钟重新计算。可信基准只选已完成 regular bar 且 `closed_at <= quote_at` 的最后一根。
- [x] **Step 4: 重跑同一命令和 `tests/test_core.py`。** 预期 PASS，新闻/outbox 行为未变。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: persist session quotes with freshness checks"`。

## Task 3: 长桥快照、推送和四时段预警

**Files:** Create `tests/test_longbridge_quotes.py`, `tests/test_session_alerts.py`; Modify `app/connectors.py`, `app/engine.py`, `app/signals.py`, `app/main.py`, `tests/test_core.py`。

**Interfaces:**
- Consumes `resolve_instrument`、`Quote`、DB 报价接口。
- Produces `longbridge_symbols(watchlist: dict) -> dict[str, str]`（供应商代码到内部代码）、`decode_longbridge_quotes(symbol: str, event: object, instrument: Instrument, received_at: float) -> list[Quote]`（同时接受快照与推送）。
- `Engine.on_quote(quote: Quote) -> None`；`PriceRules.feed(symbol: str, price: float, now: float | None = None, *, session: str = 'unknown') -> list[dict]`；返回 key 包含 symbol/session/rule，预警含 currency/session/quote_at/source。旧仅价格回调测试改成显式 Quote，不能为了保留测试猜测 regular。

- [ ] **Step 1: 添加失败测试。** `test_snapshot_and_push_preserve_all_sessions` 断言快照四个字段独立保存、SDK enum 转换准确、KR 不订阅 `.US`；`test_cancelled_generation_cannot_write` 断言回调已排队后取消不会写 DB 或 outbox；`test_session_windows_and_cooldowns` 断言如下：

```python
assert rules.feed('NVDA', 101, 1000, session='regular') == []
assert rules.feed('NVDA', 99, 1001, session='overnight') == []  # 不跨时段比较
assert len(rules.feed('NVDA', 99, 1002, session='regular')) == 1
assert rules.feed('NVDA', 101, 1400, session='regular') == []  # >370 秒重建基准
```

另测 sessions 筛选、约 5 分钟窗口、每规则冷却互不干扰、重复/倒序/过期/unknown/异常价不产生预警，热更新规则立即生效。用假 SDK 与临时 DB，不连接账户。

- [ ] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_longbridge_quotes.py tests/test_session_alerts.py -q`。
- [ ] **Step 3: 实现快照与推送。** 同步夜盘环境变量；获取快照后订阅，推送使用 SDK `trade_session` 和 `timestamp`，不按墙钟判断会话。按 [SDK enum](https://longbridge.github.io/openapi/python/reference_all/#longbridge.openapi.TradeSession) 映射 Intraday/Pre/Post/Overnight 到 regular/pre/post/overnight，未知值保持 unknown。停止标记在 SDK callback 和事件循环实际写入前都检查，失败重连也失效旧标记。报价正常存储成功且鲜度合格才交规则；历史快照只展示、首笔无穿越预警。缓冲、前价、change_state、last_fired 按时段和规则隔离，长缺口重置窗口与前价但保留冷却。通知明确币种、时段、报价时间和来源；每条规则的 outbox key 使用该规则的冷却周期。
- [ ] **Step 4: 重跑同一命令加 `tests/test_core.py tests/test_settings.py`。** 预期 PASS；保存夜盘和映射变化只重启相关长桥任务，日志不泄露凭证。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: support four-session Longbridge price alerts"`。

## Task 4: 历史 K 线的时段与收盘语义

**Files:** Create `tests/test_session_candles.py`; Modify `app/market.py`, `app/market_sessions.py`, `tests/test_research.py`。

**Interfaces:**
- Consumes `Instrument`、`reference_close`。
- Produces `bar_is_complete(timestamp: float, interval: str, instrument: Instrument, periods: dict | None, now: float) -> bool`、`classify_bar_session(timestamp: float, periods: dict | None) -> str`；`normalize_bars(rows: list[dict], interval: str, *, instrument: Instrument | None = None, periods: dict | None = None, now: float | None = None) -> list[dict]` 保留会话与可信 closed_at。
- `MarketData.candles(symbol: str, interval: str = '1d', provider: str = 'yahoo', session: str = 'regular') -> dict`；结果增加 `instrument`、`session`、`coverage`（supported_sessions/actual_sessions/notice）、`timezone`，保留原键。缓存键 `(symbol, provider, interval, session)`，配置/凭证变化失效。

- [ ] **Step 1: 添加失败测试 `test_yahoo_session_request_and_cache`、`test_dst_midnight_and_early_close`、`test_missing_periods_are_conservative`。** 模拟 Yahoo tradingPeriods/currentTradingPeriod：pre 查询开启 includePrePost；regular 与 all 缓存不互通；美国夏令时切换的两组 UTC 时间仍正确归类；提前收市 13:00 后日线完成；当前日/周元数据缺失为 incomplete；同一午夜附近时间在东京、纽约分别判定。断言 unknown 分钟 bar 不冒充 pre，非分钟 pre/post/overnight 明确抛 MarketError，Yahoo overnight 明确不支持。

```python
# test_yahoo_session_request_and_cache
assert captured_request.url.params['includePrePost'] == 'true'
assert regular_result['session'] == 'regular' and all_result['session'] == 'all'
# test_missing_periods_are_conservative（固定 now 与 timestamp 在相同市场日期）
assert current_daily['complete'] is False and current_weekly['complete'] is False
```
- [ ] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_session_candles.py tests/test_research.py -q`。
- [ ] **Step 3: 实现上述接口并升级 Yahoo/Alpaca。** 供应商路径使用 `urllib.parse.quote(..., safe='')`；Yahoo 使用供应商交易区间标记会话，不写死交易日。仅返回真实 bars；当前日/周缺少实际结束依据保持保守，已过去历史周期按市场日期判定但不伪造 closed_at。Alpaca 仅承诺 regular 并说明 IEX 范围，其他请求提示长桥。币种不符返回显式数据错误。演示数据保持明确合成标签，并采用选定标的元数据。
- [ ] **Step 4: 重跑同一命令。** 预期 PASS；指标、下一根开盘成交与无未来数据的旧测试仍通过。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: normalize historical sessions and market close times"`。

## Task 5: 长桥历史行情适配

**Files:** Create `app/longbridge_market.py`, `tests/test_longbridge_market.py`; Modify `app/market.py`。

**Interfaces:**
- Consumes `Instrument`、`Quote`、标准 Candle 与现有 `lb_config()`。
- Produces `longbridge_candles(settings: object, instrument: Instrument, interval: str, session: str) -> dict`（async）；`decode_candlesticks(rows: list[object], instrument: Instrument, interval: str, now: float) -> list[dict]`。

- [ ] **Step 1: 添加失败测试 `test_longbridge_history_sessions_and_limits`。** 假 SDK 断言分钟请求第 5 个位置参数为 `TradeSessions.All`、count 不超过 1000，5m/1h/1d/1wk 分别映射 Period.Min_5/Min_60/Day/Week；bar 的 session 原样转换；overnight 开关关闭时明确错误；SDK 缺失、映射缺失或权限失败返回可理解且不含密钥的 MarketError，不能退回 demo。日/周查询使用常规供应商 OHLC。

```python
# test_longbridge_history_sessions_and_limits
assert captured_args[0] == 'NVDA.US' and captured_args[1] == Period.Min_5
assert captured_args[2] <= 1000 and captured_args[4] == TradeSessions.All
assert {bar['session'] for bar in result['bars']} == {'overnight'}
```
- [ ] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_longbridge_market.py -q`。
- [ ] **Step 3: 实现历史适配。** SDK 延迟导入，同步调用放 `asyncio.to_thread`；依据已核对的 [官方 candlesticks 接口](https://open.longbridge.com/docs/quote/pull/candlestick) 获取最近最多 1000 根，声明查询范围限制。SDK 调用用第 5 个位置参数，避免官方示例 trade_session 与 [SDK 签名 trade_sessions](https://longbridge.github.io/openapi/python/reference_all/#longbridge.openapi.QuoteContext.candlesticks) 的差异；分钟采用 All、日/周采用 Intraday。仅按返回标签过滤延长时段；未知 bar 不归入指定会话。历史源独立标明，夜盘开关/认证改变清缓存；请求期间配置改变不能缓存为新配置数据。
- [ ] **Step 4: 重跑该测试加 `tests/test_session_candles.py`。** 预期 PASS，无实际 SDK 网络访问。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: add Longbridge session candle adapter"`。

## Task 6: API、实时总览和回测边界

**Files:** Create `tests/test_market_api.py`; Modify `app/main.py`, `app/market.py`, `app/engine.py`。

**Interfaces:**
- Consumes 上述标的、报价、K 线接口。
- Produces `/api/watchlist` 的 instruments；`/api/quotes` 增加 sessions（items 保留）；overview/candles/backtest 的 `session=regular` 参数与 `longbridge` provider。
- `MarketData.overview(symbols: list[str], provider: str = 'yahoo', session: str = 'regular') -> list[dict]` 返回每标的 instrument/currency/timezone、public_quote、session_quotes、display_quote、reference_close、change_pct、source/error。选定会话无价时显示空值；regular 公共报价可作为明确标明的后备；all 优先最新鲜长桥，不能跨时段冒充。

- [ ] **Step 1: 添加失败测试 `test_market_api_session_defaults_and_metadata`、`test_quote_reference_precedes_quote`、`test_mixed_overview_failure_isolation`、`test_backtest_market_guards`。** 断言旧无 session 请求仍 regular；fresh 长桥主价优先，stale 标签正确；一条日线 close 在报价之后必须排除；所有公共请求失败时 fresh 长桥四卡仍可显示；一个标的失败不吞其他结果；未知 session 422；JP/CN/KR、index、延长时段成交回测 422 且原因明确，美股 regular 原回测通过。非美股保留指标与行情查询。

```python
# test_market_api_session_defaults_and_metadata
assert default_result['session'] == 'regular' and default_result['instrument']['currency'] == 'USD'
# test_quote_reference_precedes_quote
assert overview_item['reference_close']['closed_at'] <= overview_item['display_quote']['quote_at']
# test_backtest_market_guards
assert jp_backtest_response.status_code == 422 and overnight_backtest_response.status_code == 422
```
- [ ] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_market_api.py tests/test_research.py -q`。
- [ ] **Step 3: 实现路由与运行集成。** 用统一解析取标的，不扩大为任意字符串；保持认证和既有返回键。先检查回测资格再请求上游。overview 合并数据库各时段报价，币种和基准时间可见；未知参考价为 null。新闻行情快照采用相同鲜度/币种/时段语义。热应用标的映射、夜盘或认证变更时清行情缓存、重订阅且失效旧回调。
- [ ] **Step 4: 重跑该命令加 `tests/test_login.py tests/test_settings.py`。** 预期 PASS。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: expose session quotes and guarded research APIs"`。

## Task 7: 美股四时段页面与统一格式

**Files:** Modify `app/assets/index.html`, `dashboard.js`, `settings.js`, `dashboard.css`, `tests/frontend_settings.test.cjs`; Create `tests/frontend_market.test.cjs`、`tests/frontend_helpers.cjs`。

**Interfaces:**
- Consumes API 的 instrument/coverage/session_quotes/display_quote；市场页与策略页共享 `state.session` 但有明确查询控件。
- Produces `renderSessionQuotes(quotes: dict, instrument: dict) -> void`、`formatMoney(value: number, currency: string) -> string`、`barDate(value: number, short: boolean = false, timezone: string = 'America/New_York') -> string`；所有行情/交易调用显式传入结果的 instrument.timezone。CSV 添加 currency、market、timezone、session 列，损益列为 `net_pnl`，UTC 时间列保留。
- 提取现有 DOM workspace helper 到 `tests/frontend_helpers.cjs`，导出 `{workspace, tick}`；使用 `RADAR_TEST_PYTHON` 优先、否则 Windows/Unix venv 探测；原测试与新测试共用，不引入应用构建依赖。

- [ ] **Step 1: 添加失败 DOM 测试。** `four session cards show source time freshness and missing prices` 断言四卡及“暂无报价”；`session switch sends session parameter and ignores late response` 断言晚到 regular 响应不覆盖 overnight；`instrument editor saves metadata and rule sessions` 断言保存 payload；`currency timezone and CSV follow result instrument` 断言选中标的切换后金额、日期和导出一致。覆盖 stale 卡到期更新、regular 后备说明、夜盘开关、设置未加载时不可保存、手机宽度布局控件可见。

```javascript
// four session cards show source time freshness and missing prices
assert.equal(app.document.querySelectorAll('[data-quote-session]').length, 4);
// session switch sends session parameter and ignores late response
assert.equal(new URL(lastMarketRequest, 'http://radar.test').searchParams.get('session'), 'overnight');
// instrument editor saves metadata and rule sessions
assert.deepEqual(saved.watchlist.price_rules[0].sessions, ['overnight']);
```
- [ ] **Step 2: 运行失败验证。** `node --test tests/frontend_settings.test.cjs tests/frontend_market.test.cjs`；新增 UI 断言失败。
- [ ] **Step 3: 实现页面和 helper。** 增加长桥历史源、会话选择和四时段卡；名称、币种、来源、市场时区显式显示，界面更新时间不冒充上游时间。刷新 quotes 不等待慢 K 线；按标的/provider/session 请求代次丢弃旧响应。设置页提供标的元数据编辑、规则时段勾选和夜盘开关；图表、净值、交易表、CSV 移除固定 USD/美东标签。所有配置字段继续 escape，sources 显示无实时数据的市场状态。修改资源版本使已打开页面可刷新新脚本。
- [ ] **Step 4: 重跑同一 DOM 命令并做本地浏览器检查。** 预期 PASS；检查市场、策略和设置页在桌面/窄屏的四卡、选择控件和错误状态，无控制台异常。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: display four sessions and instrument-aware formatting"`。

## Task 8: 其他市场历史行情与新闻关联

**Files:** Create `tests/test_multimarket_history.py`; Modify `app/market.py`, `app/signals.py`, `app/ai_client.py`, `tests/test_settings.py`, `tests/frontend_market.test.cjs`。

**Interfaces:**
- Consumes US 四时段交付后的 `Instrument`、session K 线/API/页面。
- Produces Yahoo `.KS/.KQ/.T/.SS/.SZ/^N225` 历史查询和配置市场的 AI 关联语义，不新增实时连接器。

- [ ] **Step 1: 添加失败测试 `test_other_market_history_metadata_and_indicators`、`test_currency_mismatch_and_single_market_failure`、`test_ai_multimarket_symbol_validation`。** 模拟 005930.KS、035720.KQ、7203.T、^N225、600519.SS、000001.SZ；断言前导零和 URL 编码、KRW/JPY/CNY、对应 IANA timezone、5m/1h/1d/1wk 真实 bars 与指标；没有午休/假期 bars 时不补造；meta 币种与配置不符明确错误；API 一个市场失败不影响美股。AI 接受合法内部代码，拒绝路径注入；DOM 混合自选、市场错误和非 US 回测提示可见。

```python
# test_other_market_history_metadata_and_indicators
assert korean['symbol'] == '005930.KS' and korean['instrument']['timezone'] == 'Asia/Seoul'
assert japanese['currency'] == 'JPY' and chinese['currency'] == 'CNY'
assert len(korean['bars']) == len(provider_rows)  # 不补造交易日/午休
# test_ai_multimarket_symbol_validation
assert Analysis(**{**valid_analysis, 'affected_symbols':['005930.KS','^N225']}).affected_symbols == ['005930.KS','^N225']
```
- [ ] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_multimarket_history.py -q` 和 `node --test tests/frontend_market.test.cjs`。
- [ ] **Step 3: 完成其他市场接入。** Yahoo 使用元数据与映射、market timezone 和供应商实际闭市信息；说明分钟历史范围和延迟，不对其他市场追加 `.US`。新闻关联共用 norm_symbol；AI 输入带配置市场/内部代码，提示不再限制美股，输出继续 schema 校验。示例/页面给出上述市场代码，但不自动改动用户自选或承诺当地新闻覆盖。
- [ ] **Step 4: 重跑任务命令和 `tests/test_research.py tests/test_settings.py`。** 预期 PASS；其他市场只有历史行情和指标，成交回测仍明确受限。
- [ ] **Step 5: 提交任务文件。** `git commit -m "feat: add Korean Japanese and Chinese historical markets"`。

## Task 9: 回归验收与交付记录

**Files:** Modify `README.md`, `PRODUCT.md`, `CHANGELOG.md`, `.env.example`, `.github/workflows/tests.yml`；仅按已实现行为更新。

**Interfaces:** Consumes 前 8 任务；Produces 可复现的本地/CI 验收与账户验收边界。

- [ ] **Step 1: 更新使用说明。** 记录 SDK 安装、四时段来源、夜盘开关、鲜度和价格规则、市场代码/币种、分钟范围及回测限制；补充 Windows 启动与 pytest/DOM 命令。CI 使用 Python 3.12、Node 24.13+、jsdom 29.1.1，执行全部前端 `*.test.cjs` 并覆盖 `codex/**` 分支。
- [ ] **Step 2: 执行完整离线验证。** `.\.venv\Scripts\python.exe -m pytest -q`、`node --test tests/*.test.cjs`、`git diff --check`；预期零失败、无真实账户请求。记录真实输出与测试数量，不以计划代替结果。
- [ ] **Step 3: 重启本地服务并检查。** 保留 `.env`、数据库和用户配置；健康检查、登录、四卡、默认 regular 历史查询和旧新闻仍可用。缺少 SDK/凭证时页面明确提示；真实报价权限、四时段推送和延迟单列为待账户验收，不宣称已连接。
- [ ] **Step 4: 请求整个改动的独立代码审查并处理有效问题，再重复受影响检查。** 根据用户选择的执行方法应用对应技能，不能跳过迁移、取消竞态和时间边界审查。
- [ ] **Step 5: 提交文档/CI并汇报。** `git commit -m "docs: document session and multi-market verification"`。不自动合并或推送；报告测试证据、账户验收限制和运行地址。

## 自审结果

设计覆盖：统一标的/旧配置→1；报价鲜度/迁移/基准→2；快照、取消与预警隔离→3；时段/收盘/DST→4；长桥历史→5；API/来源/回测边界→6；币种/时区/CSV/设置→7；其他市场历史/指标/AI→8；说明与验收→9。Review Focus 五项均已绑定测试。X 计划以任务 1 的统一解析接口为依赖，后续复用同一新闻/设置接口。
