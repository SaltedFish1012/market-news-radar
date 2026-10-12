# 聪明钱与 X 侧栏验收记录

执行日期：2026-10-12。执行基点：`920cfa74302f99fec8b4b246f9097104759bc594`，分支 `codex/market-sessions-x`。

## 自动验证

- 后端：`.venv/Scripts/python.exe -m pytest -q`，审查修复后 182 通过，仅一项既有 Starlette/httpx 弃用警告。
- 前端：`node --test tests/*.test.cjs`，37 通过。
- 覆盖：原数据库可加表、配置兼容、事务回滚、精确十进制、季度基准、修订与省略、所有权和足注、限速与响应上限、持久队列、取消与热重启、认证、筛选先于分页，以及旧响应丢弃。
- 导航既有六页测试更新为七页，新增 `/smart-money` 直达验证。

## 浏览器隔离验收

独立数据库使用明确标注“离线验收”的机构、证券与 X 作者，所有外部采集、AI、通知关闭。包括相邻季度增减/退出、缺基准、NEW HOLDINGS、RESTATEMENT、未知修订、旧单位、PRN/期权、P/S/A/M/F/G、衍生证券、未知金额、多报告人和 4/A。此数据不代表真实机构当前持仓或真实 X 用户最新发帖，未写入原生产数据库。

| 视口 | 页面 | 文档宽度 | 布局 |
| --- | --- | --- | --- |
| 1440 | `/`、`/news`、`/smart-money`、`/settings` | 1436 | 无横向溢出 |
| 390 | 同上 | 386 | 无横向溢出 |

市场与新闻页显示右栏聪明钱摘要；详细页隐藏摘要；设置页隐藏研究侧栏。机构/内部人切换、代码 F 筛选、足注展开、持仓下一页、新筛选回到第一页、设置聪明钱面板、浏览器后退均通过。恶意作者/足注文本显示为文字，未产生 img/script 元素。浏览器无新增 console 错误或警告。X 默认账号 `aleabitoreddit`；本机缺 Token 的测试按钮正确提示配置。

截图保存在工作树忽略目录 `data/preview/`：`smart-money-desktop-top.png`、`smart-money-mobile-top.png`、`smart-money-insiders.png`。

## 独立审查与运行

一次全功能独立审查使用 gpt-6-astra，范围 `920cfa7..1d2804b`。无 Critical、无 Minor；三个 Important 在同一次修复中解决，提交 `2373e4a`：

1. 已发现但未归档的修订及发现失败暂停相关季度的变化推断，避免误标退出；`test_pending_amendment_suppresses_exit_inference` 先失败后通过。
2. 复用已归档/已解析报告期，失败文件轮转，避免超过 20 份文件后永久饥饿；`test_unknown_period_budget_progresses_across_rounds` 和 `test_permanent_failures_do_not_starve_later_pending` 均先失败后通过。
3. 官方代码表普通失败不阻断机构和显式发行人 CIK，单独报告代码表状态；403/429 仍全局冷却；`test_ticker_failure_does_not_block_institutions_or_explicit_issuers` 先失败后通过。

修复后的完整回归为 182 后端 / 37 前端通过，未再发起审查。

服务已从当前工作树在 `http://127.0.0.1:8787/` 重启。数据库沿用 `E:/otherProj/market-news-radar/data/radar.db`，配置沿用原 `config` 目录，访问凭据沿用此前复制到工作树的原 `.env`。核对原 launcher 80844 / Python 68040 后停止；新 launcher 69968 / Python 83032，进程参数确认为本工作树的 uvicorn、127.0.0.1、8787。

SQLite 备份 API 已生成 `E:/otherProj/market-news-radar/data/radar-before-sec-x-20261012-170029.db`，备份和运行数据库完整性检查均 `ok`。171 条原新闻（ID <= 15547）的 id/event_key/title/summary 摘要保持不变：`57b2cea6f5ae9f9bc151ff7c30ade0b7299d14991b8ff52d018f73c63b4ae3e7`；后续 RSS 新增不纳入该旧数据摘要。

沿用原口令验证 `/health`、`/smart-money`、`/api/x/posts` 和三个聪明钱 API 均 HTTP 200。生产 SEC 申报数 0、X 帖数 0，离线样本未混入原数据库。未主动发送通知；本机尚无已配置的通知渠道。

本机 X Token 和 SEC 联系邮箱均未配置，两个采集开关保持关闭。指定账号 `@aleabitoreddit` 的真实最新帖子和真实机构披露仍待用户在本机设置中配置后验收；离线样本不作为真实采集证明。

## 审查范围决定

审查者明确未判断的三项均已逐项处理，无延后的 Minor：

- 真实认证 X/SEC 采集：凭据缺失，保留待配置限制。若判断错误，可能遗漏授权或上游响应边界问题。
- 生产备份、重启和旧新闻保存：纳入本轮交付前检查并完成上述验证。若检查错误，原服务可能仍载入旧代码或旧新闻可能被改动。
- 既有美股四时段/市场行为：不纳入这次 X/SEC 差异的独立审查，完整回归仍覆盖。若判断错误，既有行情中不属于本次差异的缺陷可能未被发现。
