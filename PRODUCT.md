# Market News Radar

**多市场行情与新闻研究工作台 · A workspace for multi-market history, U.S. sessions and attributed news**

[中文介绍](#中文介绍) · [English overview](#english-overview)

## 中文介绍

### 产品定位

Market News Radar 将财经新闻、自选股行情、K 线图、技术指标、策略信号和历史回测整合到一个网页工作台。用户可以从新闻事件出发，查看相关股票的价格变化，再用明确的策略规则研究历史表现。

产品适合希望集中跟踪美股、研究技术策略的个人用户，也适合用于本地数据分析与功能验证。既可以在自己的电脑运行，也可以使用 Docker 部署到服务器。

### 核心功能

| 页面 | 可以做什么 |
| --- | --- |
| 市场总览 | 查看自选股价格、5 分钟／1 小时／日／周 K 线、成交量，以及 EMA、布林带和 RSI 等指标。 |
| 策略与回测 | 选择股票、周期和行情源，查看规则信号，设置初始资金、手续费和滑点，比较历史策略表现。 |
| 新闻中心 | 浏览归档新闻、筛选关联股票，并结合主题关键词、重要性规则和可选 AI 分析理解事件。 |
| 预警记录 | 查看新闻和价格规则触发的记录；可配置 Telegram、Bark 推送。 |
| 数据连接 | 查看数据源连接状态和错误信息，了解采集服务是否正常。 |
| 设置 | 管理 AI、行情与新闻、通知、自选股和预警规则，保存后应用配置。 |

各模块拥有独立页面，支持直接访问、刷新和浏览器前进后退。设置在本地持久保存，重启后可以恢复；登录时可勾选「保持登录」。

### 策略研究

内置五种规则策略：双均线趋势、RSI 均值回归、通道突破、MACD 趋势和布林带回归。每种策略展示明确的进入和退出条件，便于理解信号的来源。

回测使用已收盘 K 线生成信号，在下一根 K 线开盘价模拟成交，并计入用户设置的每边手续费和滑点。结果包括权益曲线、买入持有基准、收益、最大回撤、已平仓交易和胜率，交易记录可导出为 CSV。

当前成交回测限美股股票／ETF 常规时段，为单股、仅做多、全仓规则，允许碎股，不使用杠杆；期末持仓按收盘价估值。胜率只统计已平仓交易，结果不包含税费、融资成本和成交容量约束。

### 数据与 AI

- **行情**：支持公共 Yahoo Finance 图表数据和需要账户凭证的 Alpaca IEX 历史 K 线；长桥提供美股四时段报价／预警及最近最多 1000 根历史 K 线；其他市场通过 Yahoo 提供韩股、日股、日经和 A 股历史及指标。
- **新闻**：支持 RSS／Atom、Alpaca 新闻流、长桥股票新闻及指定 X 账号言论轮询（全部／筛选推送），保留原文、作者与引用关系，具体可用性取决于已启用的数据源和账户权限。
- **AI 分析**：可配置 OpenAI、Google Gemini、Anthropic Claude 或 OpenAI 兼容服务。各供应商分别保存模型、接口地址和密钥，支持自定义模型 ID 与连接测试。AI 用于辅助分析新闻，策略回测依照代码中的明确规则执行。
- **演示数据**：可主动选择合成行情验证图表和回测。真实数据请求失败时会显示错误，不会自动改用演示价格。

### 典型使用流程

1. 启动服务并登录，在设置中配置自选股、数据源和所需的 AI 或通知服务。
2. 在新闻中心查看事件，筛选相关股票，了解可能影响。
3. 在市场总览查看 K 线、成交量和技术指标。
4. 在策略页选择股票与周期，调整交易成本并运行历史回测。
5. 查看预警记录，按需要启用手机推送。

### 当前阶段

当前版本是可本地运行的研究原型，提供页面配置、指标、信号和历史回测。产品不提交真实交易订单；历史回测和 AI 分析不构成收益承诺。

行情覆盖、延迟与新闻时效受上游接口影响。付费数据、AI 和推送服务需要自己的账户或密钥，可能产生费用；真实账户连接与生产部署仍需按使用环境验证。

安装、运行与部署见 [README](README.md)；协作与分支规则见 [开发规范](CONTRIBUTING.md)。

## English overview

### Product purpose

Market News Radar brings financial news, watchlist quotes, candlestick charts, technical indicators, strategy signals, and historical backtesting into one web workspace. Users can start with a news event, examine the related stock's price action, and study historical performance using explicit strategy rules.

It is designed for individuals tracking U.S. stocks and researching technical strategies, as well as local data analysis and feature evaluation. It can run on a personal computer or on a server using Docker.

### Core features

| Page | What you can do |
| --- | --- |
| Market overview | View watchlist prices, 5-minute, hourly, daily, and weekly candlesticks, volume, and indicators such as EMA, Bollinger Bands, and RSI. |
| Strategies and backtesting | Select a stock, interval, and data source; inspect rule signals; set starting capital, fees, and slippage; compare historical results. |
| News center | Browse archived news, filter by related stocks, and interpret events with theme keywords, importance rules, and optional AI analysis. |
| Alert history | Review records triggered by news and price rules, with optional Telegram and Bark notifications. |
| Data connections | Inspect source connection status and errors to understand whether collection services are working. |
| Settings | Manage AI, market and news sources, notifications, watchlists, and alert rules; save and apply configuration. |

Each module has a separate page with direct access, refresh, and browser back/forward navigation. Settings are persisted locally and restored after restart. Users can select “Remember me” when signing in.

### Strategy research

Five rule-based strategies are included: EMA trend, RSI mean reversion, channel breakout, MACD trend, and Bollinger Band mean reversion. Each exposes its entry and exit conditions so users can understand where signals come from.

Backtests generate signals from completed candles and simulate execution at the next candle's opening price, including configurable fees and slippage on each side. Results include the equity curve, a buy-and-hold benchmark, returns, maximum drawdown, closed trades, and win rate. Trade records can be exported as CSV.

The current simulation uses one stock, long-only positions, full allocation, fractional shares, and no leverage. Open positions at the end are valued at the closing price. Win rate counts only closed trades; results exclude taxes, financing costs, and execution capacity constraints.

### Data and AI

- **Market data:** Public Yahoo Finance chart data and authenticated Alpaca IEX historical candles are supported. Longbridge supplies four-session U.S. quotes, alerts and optional recent history. Yahoo supports Korean, Japanese, Nikkei and Chinese historical instruments with local currency and timezone. Execution backtests are restricted to regular-session U.S. stocks/ETFs.
- **News:** Sources include RSS/Atom, the Alpaca news stream, and Longbridge stock news. Availability depends on enabled sources and account permissions.
- **AI analysis:** Configure OpenAI, Google Gemini, Anthropic Claude, or an OpenAI-compatible service. Each provider stores its own model, endpoint, and key, with custom model IDs and connection testing. AI assists news analysis; strategy backtests execute explicit rules defined in the code.
- **Demo data:** Users can explicitly select synthetic prices to evaluate charts and backtests. Failed real-data requests show an error rather than silently switching to demo prices.

### Typical workflow

1. Start the service, sign in, and configure your watchlist, data sources, and any AI or notification services in Settings.
2. Review events in the News center and filter related stocks to assess potential relevance.
3. Inspect candles, volume, and technical indicators in the Market overview.
4. Choose a stock and interval on the Strategies page, adjust trading costs, and run a historical backtest.
5. Review alert history and enable mobile notifications if needed.

### Current status

The current version is a locally runnable research prototype with configurable pages, indicators, signals, and historical backtesting. It does not submit real trading orders. Historical results and AI analysis do not promise future returns.

Market coverage, latency, and news freshness depend on upstream sources. Paid data, AI, and notification services require your own accounts or keys and may incur charges. Live account connections and production deployment still require validation in the intended environment.

See the [README](README.md) for installation, operation, and deployment, and [CONTRIBUTING](CONTRIBUTING.md) for collaboration and branch conventions.
