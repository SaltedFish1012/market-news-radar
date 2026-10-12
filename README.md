# Market News Radar

多市场行情与新闻研究工作台（本地可运行原型）  
A local-first workspace for multi-market quotes, attributed news, and rule-based backtesting.

## 中文说明

### 1) 项目简介

Market News Radar 将财经新闻、自选股行情、K 线图、技术指标、策略信号和历史回测整合到一个网页工作台。你可以从新闻事件出发，追踪相关股票价格变化，再用明确规则评估策略历史表现。

当前版本定位为研究与验证工具，不会提交真实交易订单。

### 2) 功能总览

| 模块 | 说明 |
| --- | --- |
| 市场总览 | 查看自选股价格、5 分钟/1 小时/日/周 K 线、成交量与 EMA/布林带/RSI 等指标。 |
| 策略与回测 | 选择股票、周期与行情源，查看策略信号，配置资金/手续费/滑点并运行历史回测。 |
| 新闻中心 | 浏览新闻归档，按关联股票筛选，并结合主题关键词与可选 AI 分析理解事件。 |
| 预警记录 | 查看新闻与价格规则触发记录，可选 Telegram、Bark 推送。 |
| 数据连接 | 查看各数据源连接状态和错误信息。 |
| 设置 | 管理 AI、行情与新闻源、通知、自选股与预警规则。 |

内置策略：双均线趋势、RSI 均值回归、通道突破、MACD 趋势、布林带回归。  
回测模型：已收盘 K 线发信号，下一根开盘价成交，支持手续费/滑点配置，结果可导出 CSV。

### 3) 快速开始

#### 方式 A：Docker Compose（推荐）

```bash
cp .env.example .env
docker compose up -d --build
```

启动后访问：`http://127.0.0.1:8787`  
登录口令：`.env` 中的 `DASHBOARD_TOKEN`

#### 方式 B：本地 Python 运行

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

如需长桥相关能力，可改为安装 `requirements-longbridge.txt`。

### 4) 配置要点

复制 `.env.example` 后按需填写：

- `DASHBOARD_TOKEN`：登录必需，建议使用随机长字符串。
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`：Alpaca 数据与新闻。
- `LONGBRIDGE_*`：长桥行情与新闻（可选）。
- `AI_PROVIDER` + `AI_API_KEY`：AI 新闻分析（可选）。
- `TELEGRAM_*` / `BARK_*`：推送通知（可选）。

默认端口映射在 `compose.yaml` 中为 `127.0.0.1:8787 -> 8000`（仅本机访问）。

### 5) 测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
npm install --prefix /tmp/radar-dom-tests jsdom@29.1.1 --no-save --ignore-scripts
NODE_PATH=/tmp/radar-dom-tests/node_modules node --test tests/*.test.cjs
```

### 6) 文档导航

- 产品说明：[`PRODUCT.md`](PRODUCT.md)
- 开发规范与分支流程：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)

### 7) 风险提示

历史回测和 AI 分析不构成收益承诺。  
行情覆盖、时延与新闻时效依赖上游服务；付费数据、AI 与推送服务可能产生费用。

---

## English Overview

Market News Radar is a local-first web workspace that combines:

- market quotes and candlestick charts,
- attributed news aggregation,
- rule-based strategy signals and historical backtesting.

It is designed for research and validation, not live order execution.

### Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Open `http://127.0.0.1:8787` and sign in with `DASHBOARD_TOKEN` from `.env`.

For local development, run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Related docs: [`PRODUCT.md`](PRODUCT.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CHANGELOG.md`](CHANGELOG.md).
