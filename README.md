# Market News Radar — 多市场行情与新闻研究工作台

产品概览 / Product overview：[中英双语产品介绍 / Bilingual introduction](PRODUCT.md)。

仓库使用 Git Flow 分支约定：`main` 保存发布版本，`develop` 为开发集成分支。新功能从 `develop` 创建 `feature/*` 分支，通过 PR 合并；发布和紧急修复流程见 [开发规范](CONTRIBUTING.md)。GitHub Actions 会对主分支、开发分支及相关 PR 运行后端和前端 DOM 测试。

## v0.4 四时段、多市场与 X 用户言论

市场总览提供自选股价格、5 分钟/1 小时/日/周 K 线、成交量、EMA20/50、布林带和 RSI14。新闻、策略、预警、数据连接及设置分别位于独立页面。

策略库包含双均线趋势、RSI 均值回归、通道突破、MACD 趋势和布林带回归。可设置资金、每边手续费和滑点，查看回测权益曲线、买入持有基准、最大回撤、已平仓交易，并导出 CSV。信号按已收盘 K 线计算，下一根开盘模拟成交；不下真实订单。

侧栏使用页面切换，不再滚动定位。各页可直接打开并刷新：市场总览 `/`、策略与回测 `/strategies`、新闻中心 `/news`、预警记录 `/alerts`、数据连接 `/sources`、设置 `/settings`；支持浏览器前进和后退。策略页可单独选择股票、周期与行情源，新闻页可单独筛选关联股票。

已准备好的本地环境可直接运行：

```bash
source .venv/bin/activate
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

首次设置时用 Python 3.11/3.12 创建虚拟环境，安装 `requirements.txt` 和 `pytest`。复制 `.env.example` 后，将本地路径改为 `DB_PATH=./data/radar.db`、`CONFIG_DIR=./config`，并生成至少 24 位随机 `DASHBOARD_TOKEN`。访问 `http://127.0.0.1:8787/`，输入口令登录。口令不会写入 localStorage；登录使用 HttpOnly、SameSite=Strict 签名 Cookie；勾选「保持登录」会保存 30 天的持久会话，未勾选为最长 12 小时的临时会话。Cookie 不保存口令原文；服务重启后口令不变则会话仍有效，更换口令会使旧会话失效。原有 Bearer Token API 继续支持。

数据来源：

- **公共行情 / Yahoo Finance**：无需账户，公共图表接口可能限流、延迟或中断，未承诺正式服务可用性。页面显示来源、报价时间和未收盘 K 线状态。日/周线用供应商复权价格，分钟线用原始 OHLC。[Yahoo 数据说明](https://help.yahoo.com/kb/SLN2352.html)。
- **Alpaca / IEX**：使用现有 Alpaca 密钥，通过[官方历史 K 线接口](https://docs.alpaca.markets/us/reference/stockbarsingle-1)获取 IEX 单一交易所数据，查询结束时间保守滞后 16 分钟。IEX 成交量不等于全市场成交量。
- **演示 / 合成数据**：需要主动选择，页面和 CSV 文件名标记演示。真实接口失败不会自动替换成模拟价格。演示日期不遵循交易日历，只用于功能验证。
- **长桥**：保留原有实时报价订阅、价格预警和股票新闻功能；新增可选 SDK 历史 K 线，最近最多 1000 根，按返回的时段标签筛选。

回测仅做多、单股全仓、允许碎股、不加杠杆。前 60 根用于预热，期末持仓按收盘价估值，不虚构平仓。胜率只计已平仓交易；收益包含手续费与滑点，不计税、融资和成交容量。各周期使用固定历史区间（公共行情 5 分钟约 5 日、1 小时约 3 月、日线约 1 年、周线约 5 年），滚轮缩放和拖动只改变图表显示范围，不改变回测区间。历史表现不代表未来收益。

新增接口（需要 Bearer Token 或登录会话）：

```text
GET /api/watchlist
GET /api/market/overview?provider=yahoo
GET /api/market/candles?symbol=NVDA&interval=1d&provider=yahoo
GET /api/strategies/backtest?symbol=NVDA&interval=1d&provider=yahoo&strategy=ema_cross&initial_cash=10000&fee_bps=5&slippage_bps=5
POST /api/session  {"token":"your-dashboard-token", "remember":true}
DELETE /api/session
```

新闻和自选行情每 60 秒自动刷新；K 线可用「刷新数据」更新。行情缓存为分钟线 60 秒、日/周线 300 秒。可查询自选列表之外的受支持标的代码；持久修改自选股可进入侧栏「设置」。


### 四时段与标的配置

美股支持盘前、盘中、盘后、夜盘四张报价卡，保留每个时段的来源、币种、上游时间与交易状态。价格预警只使用上游时间在 120 秒内且交易状态正常的报价；未知时间、未知时段、倒序或重复消息仅归档。每条规则可选时段，短时价格历史和冷却按时段隔离，断线间隔超过 370 秒会清短时窗口。配置 `LONGBRIDGE_ENABLE_OVERNIGHT=true` 或设置页的夜盘开关，仍需长桥账户夜盘权限；安装 `pip install -r requirements-longbridge.txt`（SDK 5.2+）。

K 线可选 `regular/pre/post/overnight/all`，默认 `regular`。延长时段须选 5 分钟或 1 小时；Yahoo 提供盘前/盘后元数据明确的分钟历史，不提供可信夜盘；长桥分钟历史按供应商返回时段过滤。日/周线使用常规时段 OHLC，不拼接延长时段。元数据缺失的时段或当前收盘状态会保守标为未知/未收盘。涨跌参考只取报价之前已确认收盘的常规日线；缺少可靠收盘时间则留空。

设置页的标的元数据统一 `symbol/name/market/asset_type/currency/timezone/provider_symbols`，支持以下公共历史行情。保留代码前导零，不补造午休、假期或缺失交易日；分钟范围与延迟受供应商限制。其他市场本轮提供历史行情及指标，实时连接与当地新闻覆盖需另行接入。成交回测限美股股票/ETF 常规时段，其他市场、指数及延长时段返回说明。

| 市场 | 示例内部代码 | 币种 | IANA 时区 |
| --- | --- | --- | --- |
| 美国 | NVDA、SPY（NVDA.US 归一为 NVDA） | USD | America/New_York |
| 韩国 | 005930.KS、035720.KQ | KRW | Asia/Seoul |
| 日本／日经指数 | 7203.T、^N225 | JPY | Asia/Tokyo |
| A 股 | 600519.SS、000001.SZ | CNY | Asia/Shanghai |

`GET /api/watchlist` 提供 instruments；`GET /api/quotes` 保留兼容 items 并新增 sessions；行情 API 支持 `provider=longbridge` 与 `session`。图表日期使用标的时区，CSV 保留 UTC 时间并包含 currency/market/timezone/session 和 net_pnl。

### 指定 X 账号

在设置 → 行情与新闻 → X 用户言论中填写本机 Bearer Token、账号及轮询间隔（默认 120 秒，允许 60–3600），再保存并启用。Token 留空保留，勾选清除才删除；不要把 Token 发到聊天。最多 20 个账号，每账号最多 50 个关键词。回复/转帖默认关闭，账号关键词为空时使用全局主题关键词，绑定代码只表示潜在关联。

- 全部推送：新鲜的新言论逐条首报，即使重要性为一般。
- 筛选推送：完整原文命中关键词，或达到现有 high/critical 重要性规则。

首次只读取最近 10 条并静默归档，不沿分页补历史。之后每页最多 100 条，每账号每轮最多 5 页，中断批次在后续轮次续读；完整批次结束才推进完成游标，存帖与首报在同一数据库事务中。超过新闻有效期、未来超过 300 秒或时间无效的帖子只归档；编辑链更新同一新闻，保留首次通知内容，不重复首报。轮询间隔不是推送时延保证，分页、限流和网络可能延迟采集。

新闻明确标记「X 用户言论」，显示作者名、@username、稳定 ID、原文链接、完整原文展开和引用/转帖/回复对象。X 原文不剥离 HTML，作为字面文本安全显示与搜索。AI 仅对满足既有重要性及额度条件的事件异步分析，区分发表行为和言论真实性。通知是摘录，完整正文始终留在本地归档。

连接页按账号显示最近成功、最新帖、权限错误和重试时间。429 尊重 Retry-After/x-rate-limit-reset，网络错误指数退避，401/403/账单错误至少等 300 秒；错误不阻止其他账号。使用 [官方用户帖子接口](https://docs.x.com/x-api/users/get-posts)，API 读取可能计费，请在 [开发者控制台](https://developer.x.com/) 设置消费上限并核查 [官方计费说明](https://docs.x.com/x-api/getting-started/pricing)；应用不会自动购买额度。

### Windows 启动与离线验证

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.\.venv\Scripts\python.exe -m pytest -q
npm install --prefix "$env:TEMP/radar-dom-tests" jsdom@29.1.1 --no-save --ignore-scripts
$env:NODE_PATH = "$env:TEMP/radar-dom-tests/node_modules"
node --test tests/*.test.cjs
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

DOM 测试需要 Node 24.13+，建议 24.19；可用 `RADAR_TEST_PYTHON` 指定测试 Python。测试使用临时数据库、MockTransport 和假 SDK，覆盖迁移、鲜度、DST、提前收市、跨午夜、四时段、分页、编辑去重、取消与安全渲染，不发送真实通知。真实长桥权限、X Token/账单、真实采集与推送延迟需用户配置后单独验收。

### 页面设置

也可直接访问 [本地设置页](http://127.0.0.1:8787/settings)。表单独立加载，不等待行情请求；页面资源使用内容版本号，更新后重新载入会获取对应脚本。

侧栏「设置」分为 AI 分析、行情与新闻、推送通知、自选与规则。点击「保存并应用」后即时生效，受影响的采集连接会重新建立，其他来源继续运行。新 RSS 来源首次采集只归档历史文章。

- AI 供应商可选 OpenAI、Google Gemini、Anthropic Claude、OpenAI 兼容接口，提供模型预设和自定义模型 ID。各供应商分别保存模型、地址和密钥；切换供应商不会串用 API Key。
- Gemini 使用[原生 generateContent 接口](https://ai.google.dev/api/generate-content)，默认地址 `https://generativelanguage.googleapis.com/v1beta`；Claude 使用原生 Messages；OpenAI 使用 Responses。DeepSeek、OpenRouter 等网关可选「OpenAI 兼容接口」，填写其基础地址与模型 ID。本机无认证服务需要时可填写占位 Key。
- 「测试 AI 连接」使用当前表单中的 AI 配置发送一条合成消息，验证连接和分析 JSON 格式；不会保存配置，可能产生一次调用费用，也计入本地每小时上限。真实模型权限与额度需用自己的 Key 实测。
- 可配置默认行情源、Alpaca/长桥、RSS 来源及间隔、SEC 联系标识、Telegram/Bark、新闻关键词、自选股票和价格预警。长桥 OAuth 仍需在本机先完成授权，且需安装可选 SDK。
- 已保存密钥不会返回页面，输入框留空表示保留；勾选「清除」才删除。只会保存当前选中的 AI 供应商；其他供应商切换前的表单草稿可在本次页面内保留。

设置写入数据库同目录的 `settings.json`（本地为 `data/settings.json`，Docker 为 `/data/settings.json`），POSIX 文件权限为 `0600`（Windows 使用本机文件权限），通过临时文件与原子替换保存，重启后自动恢复。该文件包含密钥，应随数据卷妥善保管，不要提交仓库。首次配置从 `.env` 和 `config/*.json` 导入，保存后以页面设置为准；原文件不会被改写。访问口令、数据库路径和配置目录仍由 `.env` 管理。要恢复文件初始配置，停服务后备份并移走 `settings.json` 再启动。

设置 API 均需要登录会话或 Bearer Token：`GET /api/settings`、`PUT /api/settings`、`POST /api/settings/test-ai`。更新与测试需携带读取到的 `revision`，避免其他页面覆盖修改；版本冲突返回 409，需要重新载入。

以下保留原有采集、通知及部署说明。

**目的**：云服务器上常驻的重大财经新闻预警 + 股票关联 + AI 初步分析 + 长桥实时价格警报 + iPhone/Telegram 通知。**不交易、不下单**。

**当前交付**：可以运行的 v0.4 原型及离线测试。它不是已完成生产验收的交易系统；未使用你的 Alpaca / Longbridge / AI / 推送账户做任何真实连接测试，也不承诺事件到推送的秒级时延。新闻覆盖与时延受上游数据源限制。

## 一、第一步：验证 Alpaca 实时新闻订阅权限（建议先做）

1. 安装 Python 3.11/3.12：

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 在 `.env` 中填好 `ALPACA_API_KEY` 和 `ALPACA_SECRET_KEY`（不要发送给其他人），然后：

```bash
PROBE_SECONDS=60 python -m app.alpaca_probe
```

应当看到 `connected`、`authenticated` 和 `SUBSCRIPTION: ['*']`。**`subscribed: true` 才说明服务器成功订阅全新闻频道**。如果出现 `unauthorized` / `insufficient subscription`，按供应商权限提示处理。`news_received: 0` 只说明这 60 秒没有收到新闻，不足以判定权限失败。

接口官方文档：https://docs.alpaca.markets/us/docs/streaming-real-time-news

也可以在构建 Docker 镜像后运行 `docker compose run --rm radar python -m app.alpaca_probe`。Docker 探测必须能访问外网。

## 二、Docker 部署到 Ubuntu 云服务器

建议 2 vCPU / 2–4 GB RAM / 20 GB SSD。首先安装 Docker Engine 和 Compose Plugin。

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
```

将生成的随机值填入 `.env` 的 `DASHBOARD_TOKEN`；再填写数据源密钥、通知密钥。`DASHBOARD_TOKEN` 不应该保持示例值，至少 24 位。

```bash
sudo install -d -m 750 -o 10001 -g 10001 data
mkdir -p lb_tokens
sudo chown -R 10001:10001 lb_tokens
docker compose up -d --build
docker compose logs -f --tail=100
```

服务只绑定在服务器的 `127.0.0.1:8787`，**不要为了访问 Web 页面改成 `0.0.0.0:8787`**。在自己电脑上通过 SSH 隧道访问：

```bash
ssh -L 8787:127.0.0.1:8787 user@your-server-ip
```

打开 http://localhost:8787 ，输入 `DASHBOARD_TOKEN` 查看数据源状态、最新新闻、价格和警报。页面不会将口令写入 localStorage。

```bash
curl http://127.0.0.1:8787/health
curl -H "Authorization: Bearer <your-secret-token>" http://127.0.0.1:8787/api/status
```

若 `.env` 中没有配置通知通道，预警会存于本地 outbox 队列，页面可以查看但不会被发送。启动后进入后台点击「发送测试提醒」验证 Telegram/Bark，测试警报将明确标为测试事件。

## 三、启用通知 / AI 分析

### Telegram Bot（可选）

1. 在 Telegram 使用 BotFather 创建 Bot，获取 `TELEGRAM_BOT_TOKEN`。
2. 私聊 Bot 发起会话，获取自己的 `TELEGRAM_CHAT_ID`，填写 `.env`。
3. 重新运行 `docker compose up -d`，发送测试提醒。

### Bark（可选，iOS）

在 Bark iOS App 获取设备 Key，填入 `BARK_KEY`。默认使用 `https://api.day.app`；如采用自建 Bark 服务，修改 `BARK_BASE_URL`。推送到达也依赖苹果通知设置/网络。

### AI（可选）

进入页面「设置 → AI 分析」，选择供应商和模型，填写 API Key，可先测试连接，再启用 AI 并保存。也可用 `.env` 的 `AI_PROVIDER`、`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 设置初始值。每条有重大性的新闻先触发**未核实首报**；随后才尝试请求模型分析。本地上限默认每小时 60 次，可在设置中调整为 1–600 次。AI 不会接触你的券商登录信息。

如果关闭 AI，新闻与价格报警仍然可用，只是不会有分析补充。模型返回的结论属于假设性分析，并非事实核查结果，也不会自动生成交易指令。

## 四、长桥行情与个股新闻（第二阶段，代码已预留）

官方安装：`pip install longbridge`；Docker 镜像默认已包含。

在 `.env` 中设置：

```
LB_ENABLED=true
LB_NEWS_ENABLED=true
```

需另外完成 SDK 的 API/OAuth 授权。**行情卡已开通，并不等于服务器拿到可用的认证密钥**。本原型兼容两种 SDK 配置入口：

- 旧版 API Key 环境配置：`LONGBRIDGE_APP_KEY`、`LONGBRIDGE_APP_SECRET`、`LONGBRIDGE_ACCESS_TOKEN`；运行连接使用设置快照的 `Config.from_apikey()`，OAuth 使用 `Config.from_oauth()`；SDK 需 5.2 或以上。
- 目前推荐 OAuth：`LONGBRIDGE_CLIENT_ID`。登录前先在自己的电脑完成 `OAuthBuilder(...).build()`，将有效的 `~/.longbridge/openapi/tokens/...` 安全拷贝到云服务器 `lb_tokens` 对应目录。注意 Docker 用户的主目录与文件读权限。如未正确预授权，在无交互容器里会打印授权链接并等待，不可认为订阅已经成功。

长桥 Python SDK：https://open.longbridge.com/sdk

行情基于 `QuoteContext.set_on_quote()`，股票新闻基于 `ContentContext.news(symbol)` 定期拉取。**股票新闻为轮询接口，不是实时推送接口**。行情接入后，可在 `config/watchlist.json` 添加价格阈值与 5 分钟波动警报。

首次启用长桥时，先用 2–3 只股票验证客户端授权和心跳；不要一次订阅数百只。

## 五、配置和事件规则

- `config/watchlist.json`：股票池、主题词、关系映射、价格阈值及短时涨跌幅。
- `config/feeds.json`：新增 RSS / Atom 来源。
- `config/watchlist.json` 的 `company_map` 仅表示**可能存在关联的行业风险敞口**，不代表消息将导致这些股票上涨或下跌。
- 预警分级由透明的关键词规则初筛；模型只分析高优先级消息。真实使用前应反复调整阈值，减少误报。
- 新闻时间戳缺失、未来超过 5 分钟或超出 `NEWS_MAX_AGE_MINUTES` 的新闻**可归档但不会触发突发警报**。
- RSS 服务第一次启动只保存初始快照，不会把已有文章当作新新闻反复推送。
- 数据库位于挂载路径 `/data/radar.db`，使用 SQLite WAL；是 MVP 容量设计，不是多节点高可用架构。

例：在 `price_rules` 添加 `{"symbol":"NVDA", "kind":"price_below", "value":100, "cooldown_s":300}`。支持：`price_below`、`price_above`、`change_5m_below_pct`、`change_5m_above_pct`。价格阈值须结合当前股价自行设置，示例数字不代表当前报价。

页面「设置」保存后即时应用；修改 `.env` 的服务路径或访问口令仍需重启。已经保存页面设置时，相关 `.env` / JSON 文件修改不会覆盖它。当前没有逐笔成交统计的全市场高频扫描器。

## 六、新闻源清单与局限

默认启用：

- Alpaca News WebSocket（当配置密钥时），接口文档：https://docs.alpaca.markets/us/docs/streaming-real-time-news
- 美联储新闻稿 RSS：https://www.federalreserve.gov/feeds/feeds.htm
- SEC EDGAR 最新公告 Atom：只有设置 `SEC_CONTACT=项目名 联系邮箱` 后才允许采集，遵守 SEC Fair Access；https://www.sec.gov/about/developer-resources
- Google News 的 AI 行业主题 RSS：补充而非实时首发。

建议自行从官方 RSS 页面复制对应原始 RSS/Atom URL 后再添加：

- GlobeNewswire：https://www.globenewswire.com/rss/list （优先 Earnings / M&A / Public Companies）
- PR Newswire：https://www.prnewswire.com/rss/ （优先 Business Technology / Financial Services）

目前不自动解析这两家页面内动态按钮来猜测 RSS 地址，避免部署了失效或错误源。

### 为什么实时仍需要实测？

- 新闻 WebSocket 意味着**数据商送来后立即交付**，而非媒体首发后瞬间可见。
- 不保证免费密钥有新闻订阅权限；运行 `app.alpaca_probe` 实测。
- 长桥美股 LV1 不代表全交易所的 SIP 成交量。
- AI 结果不是新闻真实性的证明，必须保留原文链接和“已报道/未核实”状态。
- 系统记录文章发布时间（上游字段）、服务器接收时间、排队与发出通知时间以便事后评估延迟；消息推送到手机的最终到达时间无法从服务端可靠得知。

## 七、测试、问题排查

```bash
PYTHONPATH=. pytest -q   # 如未安装 pytest，pip install pytest
python -m compileall -q app
```

前端设置页的 DOM 回归测试（Node.js，测试依赖安装到临时目录）：

```bash
npm install --prefix /tmp/radar-dom-tests jsdom --no-save --ignore-scripts
NODE_PATH=/tmp/radar-dom-tests/node_modules node --test tests/frontend_settings.test.cjs
```

覆盖六个页面的独立展示、菜单导航及前进后退、策略页行情选择、新闻页股票筛选、保持登录表单，以及设置的 Gemini 切换和读取失败重试。该测试不访问实际模型或行情接口。

检查运行状态：打开首页点击「数据源状态」；或者使用 `/api/status`。

- `alpaca_news=error`: 权限、密钥、连接或订阅可能失败，看日志错误信息。
- `alpaca_news=ok`, 没新闻：短时间无消息属于正常情况。
- `longbridge_quote=disabled`: 需要启用 LB 和配置认证。
- `AI=disabled`: 没填 AI_KEY 不影响初报。
- 401 / 403：`DASHBOARD_TOKEN` 未匹配。
- SEC 403：检查自己的联系方式 User-Agent、服务器 IP 与访问频率，不要绕过屏蔽。

### MVP 与生产差距（明确待办）

1. 尚未完成第三方接口实际运行验收；无 API Key，不能伪称已通过。
2. 跨媒体事件聚类仍为最小实现，暂按源 ID 去重（AI 表述可能重复）；需要增加同义标题事件聚类。
3. 推送队列采用 SQLite 重试：已实现发出与错误重试，但未实现完整的多通道分别确认，Telegram 成功、Bark 失败可能使 Telegram 重复。
4. 长桥已加入四时段标签、上游时间鲜度检查和取消保护；实际行情权限、延迟和生产运行稳定性仍需账户验收。
5. 未处理期权行情、复杂策略和自动下单。公网服务请配置 TLS、认证、速率限制、备份与防火墙。

## 不构成投资建议

本项目为消息与行情研究工具。新闻来源可能迟到或误报，市场反应不能简单归因于单条新闻；短期期权与杠杆交易风险很高。
