# 新闻页 AI 分析展示验收

用户确认补充新闻页结果区域。本次在原始新闻卡片中展示影响方向、关联标的、模型标注的证据状态、完整依据和不确定性；顶部显示启用状态与当前可见筛选列表的数量。空结果和格式异常分别说明，历史文章不显示虚假的排队状态。

- 基线：`71ef94c`，后端 182 项、前端 37 项测试通过。
- 新增四项 DOM 回归：完整结果与转义、空结果与损坏 JSON、筛选及刷新统计、异常枚举对象/数组的隔离。
- 最终前端测试 41 项通过；后端 182 项通过，保留现有 Starlette/httpx 弃用警告。
- 浏览器使用明确标记的合成界面样例，所有 API 响应均在独立浏览器上下文拦截，不写入实际数据库、不调用模型、不推送。1440 和 390 像素宽度的文档宽度分别为 1440 和 390，页面无 JavaScript 错误；完整结果与空提示正常显示，模型文本安全转义。
- 忽略的截图：`data/preview/ai-news-1440.png`、`data/preview/ai-news-390.png`。
- 运行服务 `/news` 与全部页面资源返回 200，包含新增结果渲染和状态区域。设置版本 4：AI 已开启，模型 `gemini-3.5-flash-lite`；此时真实分析数量为 0，不将界面样例冒充实际结果。
- 实际数据库完整性为 `ok`；最初 171 条新闻的 ID、事件键、标题与摘要逐行和备份一致，原有摘要值也一致。

独立只读审查未发现 Critical 或 Minor，发现一项 Important：异常枚举对象在类型校验前转换为属性键，可能中断整页渲染。已先复现回归失败，再增加字符串类型检查修复，四种异常枚举均只影响该条新闻，其余记录与统计正常。

审查范围决定：后台触发条件、通知、历史补分析、采集器与数据库结构保持现有行为，本次不调整；X 用户言论仍在独立侧栏，本次结果区针对普通新闻；既有工作区请求并发机制没有修改，不纳入这次展示补充。没有遗留审查问题。

## 合并后验证环境

本地 `develop` 合并提交为 `e79d8c6`。前端 41 项测试通过。后端直接继承用户新增的 `.env` 时，既有 `web_client` 夹具清空密钥却继承开启的 AI，以及通知的部分配置，产生 34 项初始化错误。测试进程清空真实凭据、禁用 AI 和外部采集、使用临时数据库及 `TEST_MODE=true` 后，保留项目默认的 `NOTIFICATIONS_ENABLED=true`，后端 182 项全部通过。真实应用的设置版本仍为 4，AI 开启且模型为 `gemini-3.5-flash-lite`，新闻页返回 200。

初始化错误涉及以下既有测试（包含参数化用例）；没有修改其夹具或实际应用配置：

- `test_login.py`：`test_remember_login_sets_persistent_cookie_with_matching_server_expiry`、`test_temporary_login_retains_twelve_hour_limit_without_persistent_cookie`（None/False）、`test_persistent_session_server_rejects_expiry_and_key_rotation`、`test_logout_clears_remembered_cookie_and_https_cookie_is_secure`、`test_remember_value_requires_boolean`。
- `test_market_api.py`：`test_backtest_market_guards`、`test_quote_reference_precedes_quote`、`test_inflight_configuration_cannot_fill_new_cache`。
- `test_research.py`：`test_dashboard_session_and_research_endpoints`、`test_forged_and_expired_sessions_rejected`。
- `test_settings.py`：`test_settings_api_auth_save_hot_apply_and_conflict`、`test_draft_ai_test_does_not_save_or_change_live_settings`、`test_invalid_secret_not_echoed_in_validation_response`、`test_settings_page_visible_before_js_and_assets_versioned`、`test_each_workspace_url_has_one_visible_page_and_real_navigation`（七个页面）。
- `test_smart_money_api.py`：`test_filters_precede_pagination`、`test_api_auth_and_validation`、`test_archives_read_when_disabled_and_partial`、`test_api_never_fetches_upstream_or_exposes_contact`、`test_pending_amendment_suppresses_exit_inference`。
- `test_smart_money_runtime.py`：`test_smart_settings_restart_only_smart_job`、`test_watchlist_and_shared_contact_restart_required_jobs`。
- `test_x_runtime.py`：`test_x_settings_restart_only_x_job`。
- `test_x_views.py`：`test_preview_is_read_only`（200/402/429）、`test_preview_validation_auth_and_missing_token`。

隔离环境最初关闭通知开关时，`test_settings.py::test_revision_conflict_and_enable_validation` 的 Telegram 校验预期未触发；恢复该测试所需的项目默认通知开关后通过。以上验证失败均已查明原因并在最终隔离运行中通过。
