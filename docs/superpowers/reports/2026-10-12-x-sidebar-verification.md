# X 独立右栏验收

2026-10-12，Native 执行。实现起点 920cfa7；API 6d88c79，右栏 30ba552。

- 测试先失败再通过：6 个后端新增用例、6 个最初失败的右栏 DOM 场景；已有两项 X DOM 用例继续通过。
- `.venv/Scripts/python.exe -m pytest -q`：140 passed，1 项原有 Starlette/httpx 弃用警告。
- `node --test tests/*.test.cjs`：30 passed，0 failed。
- 临时服务 8788 使用独立数据库/配置、MockTransport 派生且明确标为“离线验收”的 X 原文；通知、AI、所有外部采集关闭。成功/402/429 预览路径均验证不修改新闻、身份、游标、通知表。
- Playwright 检查市场/新闻/设置：1440px 的 scrollWidth 为1436/1440/1436，390px均386；均不超过viewport。设置隐藏整个研究侧栏，市场/新闻显示一条fixture归档。
- 浏览器检查完整原文展开、HTML作为纯文本、默认测试账号、缺Token说明及收费提示；控制台0 errors/0 warnings。
- 截图在忽略目录 `data/preview/x-sidebar-1440.png` 与 `x-sidebar-390.png`；生产旧新闻摘要保存在 `data/preview/production-news-preservation.json`。

生产服务重启、旧新闻保留核验及一次独立整分支审查已在 SEC Task 8 完成，见 [联合交付记录](2026-10-12-smart-money-verification.md)。最终 182 后端 / 37 前端测试通过，171 条原新闻摘要保持一致。真实 X Token 未配置，本次未证明 @aleabitoreddit 最新时间线可用；公开历史嵌入与fixture均未混入生产归档。
