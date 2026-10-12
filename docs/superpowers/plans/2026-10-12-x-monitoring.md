# X 指定账号轮询与言论推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 定期读取指定公开账号，保留作者与完整原文，并支持每条都推和筛选后推送。

**Architecture:** 新增官方 X HTTP 连接器和持久采集状态，使用稳定用户 ID、帖子 ID 和编辑链去重。新闻原文与元数据独立保存，首次通知采用显式策略入口并原子归档/入队；轮询游标在归档完成后提交。复用本地设置、现有新闻中心、AI 调用额度和 Telegram/Bark outbox。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLite、httpx、原生 JavaScript/CSS、pytest、Node.js 24.13+、jsdom 29.1.1。

**Spec:** [已确认设计](../specs/2026-10-12-x-monitoring-design.md)。依赖 [行情计划](2026-10-12-market-sessions.md) 任务 1 的统一代码；按用户顺序在行情计划完成后执行。状态：实施计划待审阅。

## Global Constraints

- “消息明确标记为‘X 用户言论’，保留原文和作者身份。”正文标签采用 `X 用户言论`，不将平台认证或用户言论当作已核实事实。
- “默认 120 秒，允许 60–3600 秒”；“最多 20 个账号”；“新增账号默认 `filtered`”；回复和转帖默认 false；关键词最多 50 个。
- “账号第一次成功读取只归档最新一页（最多 10 条）作为初始快照，不推送已有帖子。”
- “每账号每轮最多取 5 页，每页最多 100 条”；“只有整批完成才推进已完成游标”。
- “超出已有 `news_max_age_minutes` 的帖子不会作为新鲜突发消息推送。”时间无效与现有未来偏差保护同样仅归档。
- “原文保留字面文本，不使用普通 RSS 的 HTML 剥离函数改写；界面按纯文本安全渲染，不执行其中的 HTML 或脚本。”
- “429：尊重 `Retry-After` 或上游限流重置时间；没有可靠时间时指数退避，最高 15 分钟。其他网络／服务端错误从 5 秒退避到最高 5 分钟。”
- “API 访问域名固定为官方域名，Token 只放 Authorization 请求头。”仅离线模拟验证，不用真实 X/AI/推送账户。

## Review Focus

1. 启用“每条都推”后旧帖、首次快照、编辑和网络重试仍不能重复通知；由任务 2、3、4 测试。
2. 归档后进程崩溃、通知队列写入失败或分页中断不能漏帖/漏首报；由任务 2、3、4 测试。
3. 用户名改名、删除后重新添加、轮询中修改参数仍以稳定 ID 恢复，不能因错误用户映射跳过新账号；由任务 4、5 测试。
4. 429 等待跨越重启、已失效分页 token 和单账号权限错误不得形成紧密重试或卡住其他账号；由任务 4、5 测试。
5. 长帖、字面 HTML、引用作者和用户名恶意显示字符必须保留完整文本且安全渲染，明确本人/被引用人的区别；由任务 3、6 测试。

## 文件与数据约定

新增 `app/x_news.py` 负责官方请求、解析、分页和调度；新增 `app/x_events.py` 负责纯文本事件、编辑身份与匹配/通知文案，避免将 `Engine` 扩成 HTTP 连接器。`settings_store.py` 定义账号输入模型；`db.py` 管理新闻迁移、编辑别名和采集状态；`engine.py` 明确区分普通新闻策略与 X 策略；`main.py` 注册/热应用 X 任务和状态。

`XAccount` 字段为 `username/enabled/push_mode/include_replies/include_reposts/keywords/related_symbols`；初次解析得到 `XUser(id, username, name, profile_url)`。内部帖子字典包括 `post_id/author/raw_text/published_at/url/references/edit_history_ids/edited_at`；references 保存 type、被引用帖子 ID、作者资料和可用原文。所有 ID 都以字符串保存，比较大小用整数，不能用字符串字典序或 JavaScript Number。

`XCursor` 保存 user_id、initialized、since_id、batch_since_id、next_token、batch_max_id、query_fingerprint、retry_at、failures、last_success_at、last_post_at、error；`XUser` 与历史 username 映射也持久化。空初始化页仍设 initialized=true，since_id 可为空，随后进入普通增量模式。

测试命令和 DOM 环境沿用行情计划；不新增 X SDK 或浏览器抓取依赖。

新增测试的 post/event/cursor、MockTransport 与临时数据库在各测试文件内构造，不能使用真实账号或本机通知设置。下列断言中的这些变量是测试准备的数据，不是额外应用接口。

## Task 1: X 设置、账号模型与密钥保护

**Files:** Modify `app/config.py`, `app/settings_store.py`, `.env.example`; Create `tests/test_x_settings.py`。

**Interfaces:**
- Consumes `normalize_symbol/resolve_instrument`。
- Produces `XAccount(BaseModel)` 及 `Settings.x_enabled: bool = False`、`x_bearer_token: str`（repr=False）、`x_poll_interval: int = 120`、`x_accounts: list[dict]`（default_factory）；Values 增加公开字段，SecretChanges/SECRETS/clear_secrets 增加 token。
- `SettingsStore.snapshot/prepare/settings/redact` 维持原签名，旧设置自动补默认值、不读时写回。环境变量使用 `X_ENABLED/X_BEARER_TOKEN/X_POLL_INTERVAL_SECONDS`；账号由本地设置管理。

- [x] **Step 1: 添加失败测试 `test_x_defaults_and_username_dedupe`、`test_x_limits_and_enable_requirements`、`test_x_secret_roundtrip_and_legacy_state`。** 断言如下，另覆盖非法控制字符、超过 20 账号/50 关键词、unsupported mode、enabled 但无 token 或无启用账号时拒绝；留空保留、显式清除、快照/异常不回显 token。

```python
assert XAccount(username='@Example').username == 'Example'
assert XAccount(username='Example').push_mode == 'filtered'
assert XAccount(username='Example').include_replies is False
assert XAccount(username='Example').include_reposts is False
# Example 与 @example 同时配置必须拒绝；interval 59/3601 必须拒绝。
```

- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_x_settings.py -q`。
- [x] **Step 3: 实现字段与校验。** username 仅允许 1–15 位 ASCII 字母/数字/下划线，去前置 @，casefold 去重。关键词去首尾空白、去重并使用既有每词 100 字符限制；绑定代码统一规范化但不自动提升重要性。开启 X 校验配置完整；默认关闭，Token 留空保留/清除遵循现有机制。
- [x] **Step 4: 重跑上述命令及 `tests/test_settings.py`。** 预期 PASS，revision、原子保存和现有 secret 行为仍通过。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: configure X accounts and protected API token"`。

## Task 2: 完整原文、编辑链与持久游标

**Files:** Modify `app/db.py`; Create `tests/test_x_storage.py`。

**Interfaces:**
- Produces `DB.x_cursor(user_id: str) -> dict`、`DB.save_x_cursor(user_id: str, state: dict) -> None`、`DB.save_x_user(user: dict, configured_username: str) -> None`、`DB.x_user(username: str) -> dict | None`、`DB.x_states() -> list[dict]`。
- Produces `DB.store_x_event(event: dict, alert: dict | None = None) -> tuple[int, str]`，状态为 `inserted/updated/duplicate`；同一事务存新闻、编辑映射与可选首报 outbox，失败全部回滚。event 包含既有新闻字段及 `source_type='x'/raw_text/x_metadata/edited_at`；x_metadata 含上文帖子字段。alert 包含 `key/title/body/severity`，key 为 `x:<编辑链初始 ID>:fast`；DB 填充实际 news_id，只在 inserted 时入队。`DB.list_rows('news')` 返回兼容默认元数据、raw_text、source_type、x_metadata（JSON 字符串）、edited_at，保持原字段。

- [x] **Step 1: 添加失败测试 `test_x_news_migration_is_repeatable`、`test_x_event_and_outbox_are_atomic`、`test_edit_chain_keeps_one_news_and_first_alert`、`test_cursor_and_user_rename_survive_restart`。** 原文 8000 字符及字面 `<b>&amp;</b>` 完整往返；同帖两次只有一条新闻/首报；模拟 alert 插入失败后新闻/编辑映射也回滚；编辑 IDs `['100','101']` 更新原 row，已有首报 body 不改；旧新闻字段默认 RSS/news，游标重开后不丢，username 改名仍指向同一 user_id。

```python
# test_edit_chain_keeps_one_news_and_first_alert（event 与 edited_event 共享根 ID 100）
news_id, state = db.store_x_event(event, alert)
assert state == 'inserted'
assert db.store_x_event(edited_event, alert) == (news_id, 'updated')
assert len(db.list_rows('news')) == 1 and len(db.due_alerts()) == 1
assert db.list_rows('news')[0]['raw_text'] == edited_event['raw_text']
```
- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_x_storage.py -q`。
- [x] **Step 3: 实现可重复迁移和方法。** 检查 table_info 后添加新闻字段；新增 x_users（含 username alias）、x_cursors、x_post_aliases。事件身份 `x:<编辑链最初 ID 或 post_id>`，已有任何 edit alias 时复用原 row；只有更高/更新版本才更新正文，不被倒序旧编辑覆盖。存帖/outbox 原子提交后再由采集器保存页进度；中间崩溃重试依靠帖子身份去重恢复。未知用户名不能自动继承别人的游标；先验证实际 user ID。
- [x] **Step 4: 重跑上述命令及 `tests/test_core.py`。** 预期 PASS，普通新闻和 outbox 未改变。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: persist X original posts edits and polling cursors"`。

## Task 3: X 事件与两种首报策略

**Files:** Create `app/x_events.py`, `tests/test_x_pipeline.py`; Modify `app/engine.py`, `app/ai_client.py`。

**Interfaces:**
- Consumes `XAccount`、DB 原子存帖接口及 `classify/matched_keywords`。
- Produces `x_should_push(post: dict, account: dict, watchlist: dict, priority: str) -> bool`、`x_alert(event: dict, account: dict, related: list[str]) -> dict`；`Engine.ingest_x(post: dict, account: dict, *, silent: bool = False) -> bool`（已归档/更新/去重都为 True，无法归档则抛错误）。现有 `ingest(item, silent=False)` 默认策略不变。

- [x] **Step 1: 添加失败测试 `test_all_mode_pushes_info_once`、`test_filtered_matches_full_text_or_importance`、`test_initial_old_invalid_and_edit_are_silent`、`test_reference_author_and_literal_text_are_preserved`。** all+info+空关键词有一条首报；filtered 正文第 1600 字之后的中文关键词可触发；账号关键词优先全局，无关键词时仅 high/critical；绑定标的不强制升级；初始化、age > news_max_age_minutes、未来 >300 秒或无有效时间仅归档；编辑不重复首报；引用帖通知区分当前作者与引用作者。AI 禁用仍完整归档与通知，AI 慢/失败不阻塞首报。

```python
# test_filtered_matches_full_text_or_importance
assert x_should_push({'raw_text':'a'*1600+'降息'}, {'push_mode':'filtered','keywords':['降息']}, {}, 'info')
assert not x_should_push({'raw_text':'普通言论'}, {'push_mode':'filtered','keywords':[]}, {'theme_keywords':[]}, 'info')
# test_all_mode_pushes_info_once
assert len(engine.db.due_alerts()) == 1 and engine.db.due_alerts()[0]['title'] == 'X 用户言论 · @Example'
```
- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_x_pipeline.py -q`。
- [x] **Step 3: 实现事件处理和文案。** X 文本不经过 strip_html，以完整 raw_text 匹配/分类，title/summary 是独立摘录。先判断时间、模式与 silent，生成可选 alert，再调用原子 DB 存储；updated/duplicate 不启动重复首报或重复 AI。标题固定 `X 用户言论 · @用户名`，正文包含作者、发布时间、明确“正文摘录”、原文链接、关联标的说明；Bark/Telegram 长度沿用当前通道限制，完整文本始终在归档。AI 保留现有重要性和请求额度条件，输入包含 source_type/作者/原文/引用关系，要求区分发表行为与内容真实性。
- [x] **Step 4: 重跑上述命令及 `tests/test_core.py tests/test_settings.py`。** 预期 PASS；现有 info 新闻仍仅归档。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: archive X speech and apply per-account push policies"`。

## Task 4: 官方读取、分页与恢复

**Files:** Create `app/x_news.py`, `tests/test_x_polling.py`。

**Interfaces:**
- Consumes `Engine.ingest_x` 和 DB user/cursor 方法。
- Produces `XClient(token: str, transport: httpx.AsyncBaseTransport | None = None)`、`async resolve_user(username: str) -> dict`、`async fetch_posts(user_id: str, account: dict, *, since_id: str | None, next_token: str | None, max_results: int) -> dict`（methods）；`async poll_account(engine: object, client: XClient, account: dict, *, now: float) -> None`。
- `XAPIError(kind: str, status_code: int | None, retry_at: float | None)` 提供无秘密错误；transport 注入仅测试，基础 URL 固定 `https://api.x.com`，不由用户配置。

- [x] **Step 1: 添加失败测试 `test_first_snapshot_is_silent_and_initialized_on_success`、`test_five_page_batch_resumes_without_gaps`、`test_failure_restarts_uncommitted_page`、`test_invalid_page_token_replays_batch`、`test_post_expansions_and_renamed_author`。** 首次 max_results=10，失败不 initialized，空成功页 initialized；正常 max_results=100，一轮第 5 页仍有 token 时 since_id 不前进，重启继续第 6 页；整数 ID `'9'/'10'` 取 10；中途 ingest 失败不提交该页进度，重试不重复通知；分页 token 失效清 token、从 batch_since_id 重放而非跳到 batch_max_id。回复/转帖开关参数及本地类型过滤生效，优先 note_post（兼容旧响应 note_tweet）长正文，引用作者不混同；请求字段采用当前官方 post.fields，不混用旧字段参数。

```python
# test_first_snapshot_is_silent_and_initialized_on_success
assert first_request.url.params['max_results'] == '10' and engine.db.due_alerts() == []
# test_five_page_batch_resumes_without_gaps（预置完成游标 10、5 页后仍有 token）
assert len(requests_this_round) == 5
assert engine.db.x_cursor('42')['since_id'] == '10'
assert engine.db.x_cursor('42')['next_token'] == 'page-six'
# test_post_expansions_and_renamed_author
assert 'note_post' in captured_request.url.params['post.fields']
```
- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_x_polling.py -q`。
- [x] **Step 3: 实现官方请求与解析。** 按 [当前用户帖子接口](https://docs.x.com/x-api/users/get-posts) 使用 `post.fields=created_at,text,note_post,edit_controls`、`expansions=author_id,referenced_posts,edit_history_post_ids`、`user.fields=id,name,username`；初始用户名查询为 `/2/users/by/username/{username}`。解析器兼容响应的 referenced_posts/referenced_tweets、edit_history_post_ids/edit_history_tweet_ids、includes.posts/includes.tweets、note_post/note_tweet，并统一为内部字段；不把旧 response alias 当成当前可请求参数。优先返回长正文，编辑时间缺失时记录本地更新接收时间并说明语义。Token 仅 Authorization，不记录请求 headers。使用稳定 ID 持久 user，响应可含 partial errors，只有完整成功页才提交进度。归档逐帖 await 成功后保存 token/max ID；整批无 next_token 才推进 since_id。query_fingerprint 改变时保留完成游标，清中断批次 token 并从 batch_since_id 重放，防止新参数与旧 token 混用；首次快照永不沿 next_token 回补旧历史。
- [x] **Step 4: 重跑上述命令及 `tests/test_x_pipeline.py tests/test_x_storage.py`。** 预期 PASS，全用 MockTransport 和临时数据库。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: poll official X timelines with resumable pagination"`。

## Task 5: 调度、退避、取消与状态 API

**Files:** Modify `app/x_news.py`, `app/main.py`; Create `tests/test_x_runtime.py`。

**Interfaces:**
- Consumes `XClient/poll_account` 和持久 cursor。
- Produces `async x_news_loop(engine: object, settings: object) -> None`；`retry_delay(error: XAPIError, failures: int, now: float) -> float`；`/api/status.configured.x_news` 与 `x_accounts`（稳定 id、公开作者、启用状态、last_success_at/last_post_at/retry_at/error，无 Token）。

- [x] **Step 1: 添加失败测试 `test_rate_limits_and_backoff_persist`、`test_single_account_error_does_not_block_others`、`test_settings_restart_only_x_job_and_cancel_pending_write`、`test_remove_readd_and_rename_keep_user_cursor`。** 429 的 Retry-After 秒/HTTP 日期和 x-rate-limit-reset 都可识别，可靠等待值不被 15 分钟本地上限截短，无可靠值指数至 900 秒；网络 5→300 秒；401/403 至少 300 秒且状态明确，账单错误不自动重试充值。重启前 retry_at 保留；取消 await HTTP/休眠立即停止，已返回数据在归档前再次检查任务状态；账号 B 在 A 错误/退避时仍采集；同用户移除重加不初始化，用户名重新分配的新 ID 必须重新初始化。

```python
# test_rate_limits_and_backoff_persist（failures=1 为第一次失败）
assert retry_delay(XAPIError('network', None, None), 1, 1000) == 5
assert retry_delay(XAPIError('network', None, None), 20, 1000) == 300
assert retry_delay(XAPIError('rate_limit', 429, None), 20, 1000) == 900
assert retry_delay(XAPIError('rate_limit', 429, 2800), 1, 1000) == 1800
```
- [x] **Step 2: 运行失败验证。** `.\.venv\Scripts\python.exe -m pytest tests/test_x_runtime.py -q`。
- [x] **Step 3: 实现循环及热应用。** 轮次按 monotonic 起始时刻安排间隔，不对每账号加固定休眠；每账号独立持久 retry_at，其他账号继续。记录最近成功轮询、最新帖时间与安全错误，无新帖仍为成功。JOB_FUNCTIONS 增加 x_news；只在 X token/开关/账号/interval 变化时重启该任务，关闭或删除账号保留历史。配置改变清无效页 token，不清完成游标；重新添加/改名需官方确认 ID，不能仅依赖永久 username alias。睡眠可取消，任务停止不再写 DB/outbox；所有 status 和 health detail 经 redact。
- [x] **Step 4: 重跑上述命令及 `tests/test_settings.py tests/test_login.py`。** 预期 PASS；修改 X 配置不重启 RSS/行情/通知任务，既有认证保持。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: schedule X polling with backoff and account health"`。

## Task 6: X 设置、新闻原文与来源状态页面

**Files:** Modify `app/assets/index.html`, `settings.js`, `dashboard.js`, `dashboard.css`; Create `tests/frontend_x.test.cjs`; Modify `tests/frontend_helpers.cjs`。

**Interfaces:**
- Consumes X settings snapshot/status/news 新字段和行情计划的 DOM helper。
- Produces `appendXAccount(account: dict) -> void`、`renderXPost(row: dict) -> string`（只生成已 escape 的文本/安全链接）；X 区域与原新闻列表共同使用既有设置保存/新闻搜索交互。

- [x] **Step 1: 添加失败 DOM 测试 `X account settings preserve secrets and submit both modes`、`X speech shows author full text references and edits safely`、`X search includes complete original text`、`X account status shows last success and permission errors`。** 默认 filtered/replies false/reposts false；token input 不回填、空保留/勾选清除；作者 name 含 HTML 和 raw_text 含 `<img onerror>` 显示为字面文本，DOM 没有恶意 img/script；8000 字原文可展开；引用/转帖角色明确、编辑标签可见；搜索能找到摘录之外的正文；展示费用提示、错误/退避与上次成功，不把无新帖判为离线。

```javascript
// X account settings preserve secrets and submit both modes
assert.equal(app.document.getElementById('setting_x_bearer_token').value, '');
assert.equal(saved.values.x_accounts[0].push_mode, 'all');
// X speech shows author full text references and edits safely
assert.equal(app.document.querySelectorAll('.x-original-text img, .x-original-text script').length, 0);
assert.equal(app.document.querySelector('.x-original-text').textContent, originalText);
```
- [x] **Step 2: 运行失败验证。** `node --test tests/frontend_x.test.cjs tests/frontend_settings.test.cjs`。
- [x] **Step 3: 实现页面。** “行情与新闻”中新增 X Token/启用/interval/账号编辑/模式/类型/关键词/绑定标的；采集启用提示 API 读取可能收费并链接 [官方计费说明](https://docs.x.com/x-api/getting-started/pricing) 和开发者控制台，不显示未经核对单价。新闻卡固定标签“X 用户言论”、name/@username、时间、原文链接、完整原文展开与编辑标记。正文用 textContent 或统一 esc，不用 HTML 剥离。sources 按账号显示成功/权限/额度/退避状态；现有 RSS 卡和页面独立加载行为保留。更新资源版本。
- [x] **Step 4: 重跑 DOM 命令和 `tests/test_x_settings.py tests/test_x_runtime.py`。** 预期 PASS；本地浏览器检查长帖、配置行在窄屏和桌面可阅读，无秘密回显和控制台异常。
- [x] **Step 5: 提交任务文件。** `git commit -m "feat: expose X monitoring settings and original speech UI"`。

## Task 7: 完整回归、说明与运行交付

**Files:** Modify `README.md`, `PRODUCT.md`, `CHANGELOG.md`, `.env.example`；仅记录已实现功能。

**Interfaces:** Consumes 任务 1–6；Produces 可复现的 X 离线验收和真实账户配置指南。

- [x] **Step 1: 更新说明。** 记录 token 与官方 API 权限、本地账号配置、首次静默/两模式/回复转帖、分页补采与有效期、编辑去重、原文与作者、费用/消费上限和错误状态；说明轮询间隔不保证推送时延。Token 只在本机设置，不要求在聊天发送。
- [x] **Step 2: 执行全项目回归。** `.\.venv\Scripts\python.exe -m pytest -q`、`node --test tests/*.test.cjs`、`git diff --check`；预期零失败、离线 MockTransport，无真实 X/AI/手机通知。确认四时段、多市场、登录和原新闻测试继续通过。
- [x] **Step 3: 重启本地服务并检查。** 默认 X 关闭；健康、登录、设置、新闻/来源页可用，旧 DB/config 读取无损。使用合成帖子/临时队列做展示检查，不写入用户生产新闻或发送真通知。真实 Token、权限、账单与采集延迟在用户启用后单列账户验收，不能用模拟结果冒充已连通。
- [x] **Step 4: 请求整个改动的独立代码审查并处理有效问题。** 必查分页/进度事务、编辑/重试去重、账号 ID 变更、取消竞态、原文渲染和 secret 日志。问题修复后重跑受影响检查。
- [x] **Step 5: 提交文档并汇报交付。** `git commit -m "docs: document X speech monitoring and verification"`；不自动推送或合并，报告实测结果、使用入口和账户验收边界。

## 实施验收记录（2026-10-12）

完整后端 134 passed、DOM 25 passed，git diff --check 无错误。独立整分支审查与一轮 RED→GREEN 修复详见行情计划验收记录；最新帖时间现在保留各页/各轮最大有效时间，空页、非法时间和旧页不会回退状态。分页、编辑链、首报事务、账号稳定 ID、取消、退避及原文安全渲染的离线回归通过。

最终服务 http://127.0.0.1:8787 保留原 DB/config/.env，登录、新闻、来源和设置可用；原 153 条新闻摘要一致。桌面和窄屏检查合成 X 帖完整原文（6629 字）、作者、编辑和引用角色；DOM 另测 8000 字 HTML 字面文本，无注入 img/script。Token 输入未回填，预览 DB 与用户 DB 分离。X、AI、长桥和真实通知连接均未配置，未发送真实通知。

Final Ruling：接受独立审查对真实账户权限、账单及延迟不作判断的范围；已核对官方接口与离线行为，用户配置后仍需账户验收。成本：账户特有权限、计费或延迟问题只能在实际启用后发现。无延期 Minor。分支保留，不自动合并或推送。

## 自审结果

设计覆盖：账号/默认值/密钥/旧配置→1；作者、原文、编辑、持久进度→2；all/filtered/时间/AI/通知→3；首次静默、分页、防漏、稳定 ID→4；取消、退避、独立错误、热应用、状态→5；设置/费用/安全新闻展示→6；说明与离线/账户验收→7。Review Focus 五项均已绑定具体测试；存帖与首报原子处理避免“先归档后崩溃导致永久漏报”。全部接口使用行情计划已定义的统一内部代码。
