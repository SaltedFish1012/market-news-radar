# SEC Smart Money Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 SEC 原始披露展示美股机构季度持仓变化与内部人交易，提供可追溯来源和独立运行状态。

**Architecture:** 配置、原始申报存储、13F/Form 4 解析、受限 SEC 客户端及采集调度分别使用小模块。认证 API 只读取本地归档；导航页面和右栏摘要使用独立请求代次。SEC 功能可独立验收，右栏摘要在 X 右栏计划完成后挂接。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、SQLite、httpx、stdlib ElementTree/Decimal、原生 JavaScript、pytest、Node 24.19/jsdom 29.1.1；不增加第三方解析依赖。

**Spec:** `docs/superpowers/specs/2026-10-12-x-sidebar-smart-money-design.md`（聪明钱页面、机构持仓、内部人交易、数据与运行、验收）。

## Global Constraints

- 只读采集、展示与筛选；聪明钱披露不自动触发手机通知或 AI 调用。旧新闻、X、价格预警、DB/config/.env 保留。
- 默认关；轮询默认 900 秒，范围 300–86400；机构最多 20 个，Berkshire CIK 1067983 仅提供可选示例。
- 机构首次最多覆盖最近两个报告季度；内部人首次最近 90 天，状态明确记录覆盖范围。
- 仅 SEC 官方 submissions/filing XML，允许主机 data.sec.gov、www.sec.gov；请求串行最多每秒 2 次；403/429 遵循 Retry-After 或退避，不绕过上游限制。
- SEC_CONTACT 必须包含可识别联系邮箱；缺失时暂停新采集，归档可读；状态不暴露联系标识或 Token。
- 13F-HR/13F-HR/A，排除 13F-NT；封面、全部信息表完整获取后才提交。仅解析 2023-01-03 以后美元单位申报，早期文件仅留来源与单位说明。
- 仅比较同机构相邻季度、完整且可解释快照；NEW HOLDINGS 为补充，RESTATEMENT 为重述；缺基准显示“缺少比较基准”，无法解释修订时不推断退出。
- CUSIP/证券类别/put-call/股数类型分别匹配；保留 SH/PRN、PUT/CALL；只用明确 CUSIP 映射，不猜代码。
- 内部人仅统一自选中的 US stock；Form 4/4-A；保留交易日、披露日、身份、代码、D/I、足注、10b5-1；衍生/非衍生分开。
- P/S 可能为公开市场或私人交易；A/M/F/G 分开；价格区间、足注价格或缺失价格的金额未知。4-A 保留修订原件，不盲目覆盖或触发主动买卖。
- 独立 sec_filings/institution_positions/insider_transactions/采集进度表；accession 去重，已验证行与对应进度事务提交，迁移可重复。
- 新接口受现有认证保护，/smart-money 页面沿用登录遮罩；页面刷新不请求 SEC 上游；部分失败显示状态及可用旧数据。
- 聪明钱专属设置只重启该任务；统一自选变化也重启它。共用 SEC_CONTACT 变化保留现有 RSS 重启规则；取消后的旧任务不得写入。

## Review Focus

1. 初次归档或上季度缺失：不能把所有行显示为新增/退出；Task 2 `test_missing_adjacent_baseline_is_unknown`。
2. 补充、重述、未知或保密省略修订：不能把不完整披露算成清仓；Task 2 `test_amendments_require_interpretable_complete_snapshot`。
3. Form 4 多报告人、共同所有权、价格足注或 4-A：保留身份和未知金额，不能重复计算主动买卖；Task 3 `test_form4_shared_owners_price_footnote_and_amendment`。
4. 请求完成同时设置重启、部分来源失败或退避：不写过期任务，不丢失败文件，其他正常来源可继续；Task 5 `test_cancel_after_fetch_before_commit` / `test_partial_failure_preserves_pending_work`。
5. 存档很多行、过滤/切页时旧响应到达：服务端先筛选再分页，前端丢弃旧响应且保留来源日期；Tasks 6–7 `test_filters_precede_pagination` / `stale smart money responses are discarded`。

---

## 文件边界与公共数据契约

新增 `smart_money_models.py`（类型与配置验证）、`smart_money_store.py`（迁移与事务）、`sec_xml.py`（安全 XML 根节点）、`sec_13f.py`（持仓语义）、`sec_form4.py`（内部人语义）、`sec_client.py`（固定主机与限速）、`smart_money.py`（采集任务）、`smart_money_views.py`（本地查询组装）、`assets/smart_money.js`（页面与摘要）。现有 config/settings_store/main/assets 只做接线；不通过 Engine.ingest 或通知队列归档这些披露。

所有解析和存储都用下列 TypedDict；数值使用有限 Decimal 的规范字符串，避免浮点误差，JSON 不含 NaN/Infinity。日期 YYYY-MM-DD，接收时间 UTC ISO，重试/成功时间 Unix 秒。

| 类型 | 必须字段（可空项显式写 None） |
| --- | --- |
| FilingRef | accession:str（10-2-6 位数字带连字符）, cik:str（1–10 位规范数字）, form:str, filed_at:str, report_period:str\|None, primary_document:str, source_url:str |
| Filing（extends FilingRef） | entity_name:str, received_at:str, amendment_type:none/new_holdings/restatement/unknown, completeness:complete/incomplete/unsupported, detail:str |
| Position | row_key:str, issuer_name:str, security_class:str, cusip:str, shares:str, value_usd:str, share_type:SH/PRN, put_call:''/PUT/CALL |
| Transaction | row_key:str, issuer_cik:str, issuer_name:str, security_title:str, owners:list[dict]（cik/name/is_director/is_officer/is_ten_percent_owner/is_other/officer_title/other_text）, derivative:bool, transaction_date:str\|None, code:str, acquired_disposed:str, shares:str\|None, price:str\|None, amount_usd:str\|None, post_shares:str\|None, ownership:str, ownership_nature:str, footnotes:dict[str,str], ten_b5_1:bool\|None |
| Progress | source_key:str（institution:CIK / issuer:CIK / sec:cooldown）, initialized:bool, symbols:list[str]（默认[]）, pending:list[FilingRef], last_success_at:float\|None, last_error:str, retry_at:float, failures:int, coverage_start:str\|None, coverage_end:str\|None, coverage_state:pending/partial/complete |
| SourceStatus | Progress 去除 pending，加 pending_count:int 与 state:disabled/setup_required/pending/ok/partial/error；不包含联系标识 |
| Snapshot | cik:str, report_period:str, complete:bool, filings:list[Filing], positions:list[Position], detail:str |
| HoldingsResult | items:list[dict]（Position + symbol:str\|None, change:new/increase/decrease/exit/unchanged/unknown, shares_delta:str\|None）, period:str\|None, previous_period:str\|None, comparison_state:compared/baseline_missing/not_comparable, complete:bool, filings:list[Filing] |

实施参数：响应解压后上限 20 MiB、timeout 20 秒、单次最多检查 50 个 XML 文档，超过边界显示不完整/错误；不悄悄截断。新配置 `smart_money_issuer_map: dict[str,str]` 为内部代码→CIK 的显式覆盖（最多 20 条），解决官方 ticker 写法与内部代码差异；关联代表发行人，证券类别仍展示 XML 原值，不猜股类。CUSIP 映射最多 200 条，键为 9 位 `[A-Z0-9*@#]`，值为合法美股内部代码。以上是已批准行为的实施边界，不引入新的采集供应商。

测试命令沿用 X 计划的 Python/Node 路径和 NODE_PATH；全部测试使用临时 DB、MockTransport、固定时间或可注入时钟，禁止真实网络、付费账户和通知。

### Task 1: 配置、类型和独立事务存储

**Files:** Create `app/smart_money_models.py`, `app/smart_money_store.py`, `tests/sec_helpers.py`, `tests/test_smart_money_store.py`, `tests/test_smart_money_settings.py`; Modify `app/config.py`, `app/settings_store.py`, `app/db.py`。

**Interfaces:**
- Consumes: 现有 `DB.conn`, `DB.lock`；SettingsStore.prepare/commit/settings/snapshot；公共契约表。
- Produces: `InstitutionConfig(BaseModel)` 的 `name:str,cik:str,enabled:bool=True`；`Settings.smart_money_enabled=False`, `smart_money_poll_interval=900`, `smart_money_institutions=[]`, `smart_money_cusip_map={}`, `smart_money_issuer_map={}`，Values 同名可选更新字段。
- Produces: `init_smart_money_schema(conn: sqlite3.Connection) -> None`；`SmartMoneyStore(db)` 的 `has_filing(accession:str)->bool`, `progress(source_key:str)->Progress`, `save_progress(progress:Progress)->None`, `commit_filing(filing:Filing, positions:list[Position], transactions:list[Transaction], progress:Progress)->bool`, `filings(*,cik:str|None=None,kind:str|None=None)->list[Filing]`, `positions(accession:str)->list[Position]`, `transactions(*,issuer_cik:str|None=None,code:str|None=None,derivative:bool|None=None,limit:int=100,offset:int=0)->tuple[list[dict],int]`, `all_progress()->list[Progress]`。
- Store 查询 transactions 行包含 Transaction 加 `accession,form,filed_at,source_url,report_period`；kind 仅 institution/insider。tests/sec_helpers 定义 `make_ref(**overrides)`, `make_filing(**overrides)`, `make_position(**overrides)`, `make_transaction(**overrides)`, `make_progress(**overrides)`，返回公共契约数据。

- [ ] **Step 1: 写失败测试**。

```python
def test_atomic_filing_and_progress(db):
    store = SmartMoneyStore(db)
    filing, progress = make_filing(), make_progress(last_success_at=1000)
    assert store.commit_filing(filing, [make_position()], [], progress) is True
    assert store.commit_filing(filing, [make_position()], [], progress) is False
    assert len(store.positions(filing['accession'])) == 1
    assert store.progress(progress['source_key'])['last_success_at'] == 1000
```

`test_invalid_row_rolls_back_filing_and_progress` 用非法数值/重复 row_key 验证整批回滚；`test_migration_is_repeatable_and_preserves_news` 两次 DB 初始化且旧新闻摘要不变。配置测试断言默认 false/900、300与86400可用、299与86401拒绝、20机构可用/21拒绝、规范 CIK 重复拒绝、非法映射拒绝、旧 settings.json 补默认、不返回密钥；开启但缺 SEC 联系邮箱允许保存，运行时显示暂停。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_smart_money_store.py tests/test_smart_money_settings.py -q`；预期新模块/设置字段缺失。
- [ ] **Step 3: 实现契约与存储签名**：schema 在 DB.__init__ 当前锁内调用，sec_filings 用 accession 主键和元数据 JSON，positions/transactions 用 accession+row_key 复合唯一键及检索字段，sec_progress 用 source_key 主键及进度 JSON；with db.lock, db.conn 原子提交，重复 accession 不追加行。数值/日期/身份及列关系在写入前验证。机构 CIK 规范化、去重，字段 extra=forbid；映射只允许美股代码，不要求已加入自选才能展示持仓；不更改现有 SEC_CONTACT 字段格式及新闻表内容。
- [ ] **Step 4: 绿灯**：上述新测试加 `.venv/Scripts/python.exe -m pytest tests/test_settings.py tests/test_x_storage.py -q`，预期全部 PASS。
- [ ] **Step 5: 提交**：`git add app/config.py app/settings_store.py app/db.py app/smart_money_models.py app/smart_money_store.py tests/sec_helpers.py tests/test_smart_money_store.py tests/test_smart_money_settings.py`；`git commit -m "feat: add smart money configuration and atomic SEC storage"`。

### Task 2: 13F 解析、修订合成与相邻季度比较

**Files:** Create `app/sec_xml.py`, `app/sec_13f.py`, `tests/test_sec_13f.py`, `tests/fixtures/sec/13f_cover.xml`, `13f_table.xml`, `13f_new_holdings.xml`, `13f_restatement.xml`（后三个也在同一 fixture 目录）。

**Interfaces:**
- Consumes: Task 1 FilingRef/Filing/Position/Snapshot/HoldingsResult。
- Produces: `parse_xml(data:bytes)->Element`（拒绝 DTD/实体、非支持编码及超限）；`parse_13f(cover:bytes,tables:list[bytes],ref:FilingRef,*,received_at:str)->tuple[Filing,list[Position]]`；`build_snapshots(filings:list[Filing],positions_by_accession:dict[str,list[Position]])->list[Snapshot]`；`compare_holdings(current:Snapshot,previous:Snapshot|None,cusip_map:dict[str,str])->HoldingsResult`。

- [ ] **Step 1: 写失败测试**：夹具保留官方 XML 层级、namespace、报告期、表总行数/金额和 amendmentType，使用本地合成数据标记；封面 `06-30-2026` 日期输出规范 `2026-06-30`，同样接受 ISO 日期。保留2026版可选字段以验证它们不会导致拒绝正常文件。

```python
def test_missing_adjacent_baseline_is_unknown():
    result = compare_holdings(current_snapshot, None, {})
    assert result['comparison_state'] == 'baseline_missing'
    assert all(p['change'] == 'unknown' and p['shares_delta'] is None for p in result['items'])
```

同名测试再传跨季度 previous，仍不能比较。`test_amendments_require_interpretable_complete_snapshot` 验证补充不删旧行、重述替换、未知类型/补充重叠 key/保密省略/信息表未齐不产生退出；`test_position_keys_units_and_mapping` 验证 SH/PRN、PUT/CALL、类别隔离，重复同 key 行合并 Decimal 股数/市值，未知 CUSIP 的 symbol=None，filed_at 2023-01-02 为 unsupported/无行、2023-01-03 不乘1000。`test_xml_rejects_entities_and_nonfinite_values` 验证 DTD、UTF-16 实体、无效金额拒绝，不产生部分快照。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_13f.py -q`；预期缺 parser/comparison。
- [ ] **Step 3: 实现上述签名**：XML 按 namespace local-name 读取，13F日期接受 MM-DD-YYYY/ISO 并验证季度末；封面/tableEntryTotal/tableValueTotal 校验不一致标为 incomplete；解析表必须成功后一起返回。row_key 为规范 CUSIP、折叠空白后的证券类别、put_call、share_type 的 JSON 数组编码；合并相同 key，保留证券原显示值。按 report_period 分组和 filed_at/accession 排序合成：完整原件建立基底；NEW HOLDINGS 只追加未重叠 key；完整 RESTATEMENT 取代基底；未知修订或缺基底停止差异推断。比较先校验精确相邻日历季度，再对两边 key 并集计算股数差；退出行当前 shares='0'，shares_delta 为负，明确来源于完整报告差异。unsupported/incomplete 仅显示原件及原因。
- [ ] **Step 4: 绿灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_13f.py tests/test_smart_money_store.py -q`；全部 PASS。
- [ ] **Step 5: 提交**：`git add app/sec_xml.py app/sec_13f.py tests/test_sec_13f.py tests/fixtures/sec`；`git commit -m "feat: interpret complete 13F snapshots and quarterly changes"`。

### Task 3: Form 4 交易语义和身份保留

**Files:** Create `app/sec_form4.py`, `tests/test_sec_form4.py`, `tests/fixtures/sec/form4.xml`, `form4_amendment.xml`（同目录）。

**Interfaces:** Consumes Task 2 `parse_xml(data)`、Task 1 FilingRef/Filing/Transaction；Produces `parse_form4(document:bytes,ref:FilingRef,*,received_at:str)->tuple[Filing,list[Transaction]]`、`transaction_kind(code:str)->str` 返回 buy/sell/grant/exercise/tax/gift/other。

- [ ] **Step 1: 写失败测试**。

```python
def test_form4_shared_owners_price_footnote_and_amendment():
    filing, rows = parse_form4(amendment_xml, make_ref(form='4/A'), received_at='2026-10-12T00:00:00+00:00')
    assert filing['form'] == '4/A'
    assert len(rows[0]['owners']) == 2
    assert rows[0]['ownership'] == 'I'
    assert rows[0]['amount_usd'] is None
    assert rows[0]['footnotes']['F1']
```

`test_transaction_codes_and_exact_amount` 验证 P/S/A/M/F/G/其他各自分类，明确100股×12.50价格='1250.00'；价格足注、区间、缺失、NaN 金额 None；`test_derivative_rows_and_ten_b5_are_distinct` 验证非衍生/衍生各一行、身份完整、同一行不因两个报告人重复存储、显式10b5-1保留，未出现时None。XML issuer CIK 与 ref.cik 不一致拒绝；4-A 标为修订并保留 report_period，不覆盖原件。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_form4.py -q`；预期缺 parser。
- [ ] **Step 3: 实现签名**：交易 row_key 用 non_derivative/derivative 与该类文档行号，不按报告人数扩展；保存每位 reportingOwner 的角色、direct/indirect、nature 和完整足注。只对数值有限、非负、无价格足注的明确 shares/price 用 Decimal 相乘；根字段 aff10b5One/aff10B5One 转 bool（local-name 比较时 casefold），未出现不能猜测。允许2026版额外可选 XML 字段。4-A amendment_type='unknown' 表示无法自动替换原行，页面显示修订；P/S 文案“买入/卖出（公开市场或私人交易）”。
- [ ] **Step 4: 绿灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_form4.py tests/test_sec_13f.py -q`；全部 PASS。
- [ ] **Step 5: 提交**：`git add app/sec_form4.py tests/test_sec_form4.py tests/fixtures/sec`；`git commit -m "feat: preserve Form 4 ownership and transaction semantics"`。

### Task 4: 固定主机 SEC 客户端、发现和限速

**Files:** Create `app/sec_client.py`, `tests/test_sec_client.py`, `tests/fixtures/sec/submissions.json`, `submissions_older.json`, `filing_index.json`, `company_tickers.json`。

**Interfaces:** Consumes FilingRef 和 `parse_xml`；Produces `SECError(kind:str,status_code:int|None=None,retry_at:float|None=None)`；`SECClient(contact:str,transport=None,*,clock=time.time,sleep=asyncio.sleep)`，异步 `submissions(cik:str)->dict`, `submission_file(cik:str,name:str)->dict`, `ticker_index()->dict[str,str]`, `filing_documents(ref:FilingRef)->tuple[bytes,list[bytes]]`, `close()->None`；纯函数 `filing_refs(payload:dict,cik:str)->list[FilingRef]` 规范化 recent 包装和补充分页顶层数组。

- [ ] **Step 1: 写失败测试**。

```python
def test_serial_rate_limit_and_retry_after(fake_clock):
    # 用 MockTransport 发三次并发调用，记录实际请求起点。
    assert starts[1] - starts[0] >= 0.5
    assert starts[2] - starts[1] >= 0.5
    assert rate_error.retry_at == fake_clock.now + 1800
```

补充 `test_fixed_hosts_paths_and_response_limits`：拒绝非法 CIK/accession、../、外部 URL、重定向、超20MiB解压数据和>50XML；User-Agent 包含联系邮箱但异常内容不含它。`test_recent_and_supplemental_submissions` 验证列长度不一致失败、旧分页顶层结构、13F-NT排除由消费方完成、file/report日期分别保留。`test_filing_documents_discover_multiple_tables` 验证按 index.json 下载全部 informationTable，不硬编码56757.xml；Form4 的 xslF345X05 及13F的xslForm13F_X02展示前缀去除后读取原始 XML。403、429 同时测试秒数及 HTTP-date Retry-After、无头退避由 Task 5 处理、404不伪造空持仓。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_client.py -q`；预期缺 SECClient。
- [ ] **Step 3: 实现接口**：唯一底层请求方法在串行锁内控制请求起点间隔>=0.5秒，禁重定向且流式限制解压字节。submissions 路径 CIK 左补零到10位；archive 路径用规范 CIK/accession 去连字符目录；primary_document、manifest文件名只允许受控相对路径，不接受传入URL；展示前缀只接受 xslF345X加两位数字或xslForm13F_X加两位数字，去除后取原始XML。JSON日期/列完整验证；信息表以 XML root local-name 识别，封面和表都齐全才返回。错误只暴露 kind/status/retry_at；抓取不自带无限重试，不从镜像绕过403/429。
- [ ] **Step 4: 绿灯**：`.venv/Scripts/python.exe -m pytest tests/test_sec_client.py tests/test_sec_13f.py tests/test_sec_form4.py -q`；全部 PASS。
- [ ] **Step 5: 提交**：`git add app/sec_client.py tests/test_sec_client.py tests/fixtures/sec`；`git commit -m "feat: add bounded SEC discovery and serial request limits"`。

### Task 5: 覆盖范围、持久队列和热配置采集任务

**Files:** Create `app/smart_money.py`, `tests/test_smart_money_runtime.py`; Modify `app/main.py`。

**Interfaces:**
- Consumes Task 1 SmartMoneyStore/Progress、Task 2 parse_13f、Task 3 parse_form4、Task 4 SECClient/filing_refs、现有 `resolve_instrument(symbol, configured)`。
- Produces: `sec_contact_ready(contact:str)->bool`、`resolve_issuer_scope(instruments:list[dict],ticker_index:dict[str,str],overrides:dict[str,str])->tuple[dict[str,list[str]],list[dict]]`；后者返回 CIK→内部代码列表和不支持/未映射代码及原因。
- Produces: `async collect_round(engine,settings,client:SECClient,*,now:float)->None`, `async smart_money_loop(engine,settings)->None`；注册 JOB_FUNCTIONS['smart_money']。单 source_key 的 Progress 使用 Task 1 全部字段，不新增隐含游标；issuer 进度的 symbols 保存本轮确认的内部代码，供离线查询。

- [ ] **Step 1: 写失败测试**。

```python
def test_cancel_after_fetch_before_commit(engine, cancelled_client):
    # cancelled_client 返回完整文件前取消当前 task；捕获 CancelledError。
    assert SmartMoneyStore(engine.db).filings() == []
    assert engine.db.list_rows('news') == []
    assert engine.db.due_alerts() == []
```

`test_partial_failure_preserves_pending_work`：机构 A 表损坏、B 成功，A accession 留在 pending，重启后仍尝试A，B不重复写。`test_initial_coverage_and_scope`：机构只最近季度及其上季（遇缺季明确partial，不用更旧季度充当相邻），内部人>=now-90天，ETF/指数/KR/JP/CN排除且显示原因；同CIK多代码只抓一次并标明发行人关联。`test_contact_missing_disabled_and_backoff`：默认关/无邮箱零网络、403/429遵循retry_at、无头30秒指数退避上限3600秒，持久失败数，旧数据可读；429/403冷却期间不访问其他SEC路径。`test_smart_settings_restart_only_smart_job`、`test_watchlist_and_shared_contact_restart_required_jobs` 验证重启集合及 stop-before-start 顺序；不重启 X/notification。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_smart_money_runtime.py -q`；预期任务未注册。
- [ ] **Step 3: 实现接口**：初次发现取最新报告期及紧邻上季，解析不明 report_period 时先获取封面确定，不能仅取两个 accession。内部人首次90天；读取 submissions.files 补充分页到边界，明确完整/partial状态。发现结果 accession 去重并持久 pending；每 source 每轮最多处理20份，未完成保留pending，失败文件不因推进日期或90天滚动丢弃。成功文件及移除该pending项一起事务提交；重复文件不重复行。取消在每次 await 后检查并在同步提交前再次检查，CancelledError向上传播，finally关闭客户端。解析/404等单来源错误继续其他来源；403/429设置持久全局冷却（sec_progress source_key='sec:cooldown'，使用同 Progress 类型），禁止继续请求。独有设置变化只重启smart_money，自选变化也重启；SEC_CONTACT变化保留RSS既有逻辑。健康状态使用常量文案，不调用 AI 或通知。
- [ ] **Step 4: 绿灯**：`.venv/Scripts/python.exe -m pytest tests/test_smart_money_runtime.py tests/test_x_runtime.py tests/test_settings.py -q`；全部 PASS，测试时显式清空外部服务凭据。
- [ ] **Step 5: 提交**：`git add app/smart_money.py app/main.py tests/test_smart_money_runtime.py`；`git commit -m "feat: collect SEC filings with durable progress and safe restarts"`。

### Task 6: 认证只读 API、筛选与来源状态

**Files:** Create `app/smart_money_views.py`, `tests/test_smart_money_api.py`; Modify `app/main.py`。

**Interfaces:**
- Consumes Task 1 Store 查询、Task 2 build_snapshots/compare_holdings、Task 5 scope/contact校验。
- Produces: `smart_money_overview(store,settings,watchlist:dict)->dict` 返回 `{configured:{enabled,contact_configured},sources:list[SourceStatus],scope:{supported:list[dict],unsupported:list[dict]},recent_filings:list[Filing],counts:{filings,holdings,insiders}}`，recent_filings最多10份；sources 包含所有配置/已归档来源状态，pending只给数量，不把完整队列返回客户端。scope 使用进度里的已确认映射及当前显式覆盖，未曾确认的股票显示待映射。
- Produces: `holdings_view(store,*,cik:str|None=None,period:str|None=None,symbol:str|None=None,limit:int=100,offset:int=0,cusip_map:dict[str,str])->dict` 返回 `{reports:list[HoldingsResult],items:list[dict],total,limit,offset}`；分页只作用于合并行列表，reports 保留各机构期间/comparison_state/filings 元数据但不重复所有items。
- Produces: `insiders_view(store,*,symbol:str|None=None,code:str|None=None,derivative:bool|None=None,limit:int=100,offset:int=0,issuer_map:dict[str,list[str]])->dict` 返回 `{items:list[dict],total,limit,offset}`，每条补 `symbols:list[str]`；issuer_map 为归档发行人与已确认代码关联，不能在查询期间拉ticker索引。
- API: 认证 GET `/api/smart-money/overview`、`/holdings`、`/insiders`，筛选参数同上；limit 1–500、offset>=0，period有效日历季度日期、code单ASCII字母，CIK/symbol按现有校验。`/api/status` 增加 smart_money 的启用/联系邮箱是否配置/覆盖/成功/错误/重试，不输出实际邮箱。unknown symbol/cik 合法但无记录时返回空，不报500。

- [ ] **Step 1: 写失败测试**。

```python
def test_filters_precede_pagination(authed_client, many_sec_rows):
    response = authed_client.get('/api/smart-money/insiders?symbol=NVDA&code=P&limit=1')
    body = response.json()
    assert response.status_code == 200
    assert body['total'] == 2 and len(body['items']) == 1
    assert body['items'][0]['code'] == 'P' and 'NVDA' in body['items'][0]['symbols']
```

`test_api_auth_and_validation` 验证三个API401及参数422，合法未知筛选200空；`test_archives_read_when_disabled_and_partial` 验证关闭/无联系邮箱/单来源错误仍能读旧数据且状态准确；`test_api_never_fetches_upstream_or_exposes_contact` 将SECClient方法设为抛异常，页面API仍成功且响应无测试Token/邮箱；持仓筛选也在分页前，缺基准/未映射/修订元数据保留。

- [ ] **Step 2: 红灯**：`.venv/Scripts/python.exe -m pytest tests/test_smart_money_api.py -q`；预期路由404。
- [ ] **Step 3: 实现签名与路由**：本地快照构造后先筛选再分页；insider返回issuer关联代码及XML原类别，4-A单独标记不计为新主动交易汇总。用 Task 5 持久进度的 symbols 构造CIK→代码映射；代码配置变更可以关联显式映射，但不得猜。sources 转为SourceStatus，contact/secret只输出bool，错误经现有config_store.redact。所有API不实例化上游客户端。
- [ ] **Step 4: 绿灯**：`.venv/Scripts/python.exe -m pytest tests/test_smart_money_api.py tests/test_research.py tests/test_login.py -q`；全部 PASS。
- [ ] **Step 5: 提交**：`git add app/smart_money_views.py app/main.py tests/test_smart_money_api.py`；`git commit -m "feat: expose authenticated local smart money research APIs"`。

### Task 7: 聪明钱页面、右栏摘要与设置编辑

**Files:** Create `app/assets/smart_money.js`, `tests/frontend_smart_money.test.cjs`; Modify `app/assets/index.html`, `dashboard.css`, `navigation.js`, `dashboard.js`, `settings.js`, `app/main.py`, `tests/frontend_helpers.cjs`。

**Interfaces:** Consumes Task 6 API、现有api/esc/safeUrl；如有X右栏，使用其 xSidebar容器。Produces `loadSmartMoneyOverview()->Promise<void>`, `loadSmartMoneyDetails()->Promise<void>`, `invalidateSmartMoneyRequests()->void`, `bindSmartMoney()->void`；独立 `smartMoneyState` 保存 tab/cik/period/symbol/code/derivative/offset/overviewGeneration/detailsGeneration。
- 页面映射 `smartMoney:('/smart-money','smartMoneySection','聪明钱')`，`smartMoneyView data-page='smartMoney'`；DOM `smartMoneyTabs`, `smartMoneyFilters`, `smartMoneyStatus`, `smartMoneyList`, `smartMoneyPagination`, `smartMoneySummary`。摘要只在市场/新闻页显示；没有X栏时为研究页侧栏同位置独立容器，不能阻止本计划独立验收。
- 设置页新增聪明钱面板，含enabled、interval、机构name/cik/enabled行、CUSIP与issuer映射JSON编辑器，复用已有SEC_CONTACT输入；保存仍使用现有整份 SettingsUpdate revision 流程。

- [ ] **Step 1: 写失败 DOM 测试**。

```javascript
test('smart money preserves disclosure dates and source attribution', async () => {
  const app = await workspace({url:'http://radar.test/smart-money', smartMoneyData});
  assert.ok(app.document.querySelector('#smartMoneyList').textContent.includes('2026-06-30'));
  assert.ok(app.document.querySelector('#smartMoneyList').textContent.includes('2026-08-14'));
  assert.ok(app.document.querySelector('#smartMoneyList a').href.startsWith('https://www.sec.gov/Archives/'));
  assert.equal(app.document.querySelector('#smartMoneyList img'), null);
});
```

helper新增smartMoneyData默认空归档和3个API stub。`stale smart money responses are discarded` 手动控制先机构后内部人、筛选修改、切页、退出后的Promise，旧数据不得覆盖；`settings persist only intended SEC fields` 验证默认false/900、机构添加/删除、示例需点击加入、非法JSON阻止提交、旧密钥不回写；`summary appears only on market and news` 验证详细页及设置隐藏摘要；`unknown baseline amendments derivatives and prices are explicit` 验证中文文案、未映射CUSIP、P/S范围、A/M/F/G独立、4-A修订、金额未知、footnote展开、完整作者身份/所有权；分页更改offset且新筛选重置0。

- [ ] **Step 2: 红灯**：`node --test tests/frontend_smart_money.test.cjs`；预期新route/nav/DOM缺失。
- [ ] **Step 3: 实现接口与接线**：加载顺序navigation/dashboard/x_sidebar（若存在）/smart_money/settings，SHA版本包含新文件。Python与JS页面映射同时增加smartMoney，并直接GET返回正确隐藏状态；登录后独立overview加载，详细API仅聪明钱页调用，摘要不等待慢行情，按现有刷新周期更新本地数据。筛选及页面切换递增代次，安全渲染所有名称/足注；明确“报告期/披露日/交易日”、披露延迟与“报告期持仓差异”，禁止实时资金流用语。未配置显示设置链接，部分失败保留归档；每个报告状态必须可见，所有SEC原件包括unsupported报告可点开。
- [ ] **Step 4: 绿灯**：`node --test tests/*.test.cjs` 及 `.venv/Scripts/python.exe -m pytest tests/test_smart_money_api.py tests/test_smart_money_settings.py tests/test_login.py -q`；全部 PASS。
- [ ] **Step 5: 提交**：`git add app/assets app/main.py tests/frontend_helpers.cjs tests/frontend_smart_money.test.cjs`；`git commit -m "feat: add SEC smart money workspace and configuration UI"`。

### Task 8: 全套验收、一次独立审查和本地运行

**Files:** Modify `README.md`; Create `docs/superpowers/reports/2026-10-12-smart-money-verification.md`；必要修复仅修改审查发现对应文件并补回归测试。

**Interfaces:** Consumes Tasks 1–7 及X计划产物；Produces可运行本地服务、配置说明、实际测试结果及审查记录；无合并/push/新付费订阅。

- [ ] **Step 1: 准备隔离验收**：fixture DB明确标记演示，包含缺基准、增减退出、NEW HOLDINGS/RESTATEMENT/未知修订、PRN/期权、P/S/A/M/F/G、衍生、未知价格、多报告人、4-A、部分失败；覆盖1440px和390px。记录生产旧新闻摘要、DB/config/.env路径及进程身份，不输出秘密值。
- [ ] **Step 2: 运行验证**：`.venv/Scripts/python.exe -m pytest -q` 和 `node --test tests/*.test.cjs` 全部PASS；隔离浏览器检查市场/新闻右栏、聪明钱两个视图/筛选/分页、设置和浏览器后退，`scrollWidth<=innerWidth`、console无新增错误。只在修改或失败后重跑受影响测试，不重复已通过的完整套件。
- [ ] **Step 3: 做一次全分支独立审查**：按 executing-plans/requesting-code-review 调用一个新审查者，审查本次X与SEC全部产品差异和两份计划；给出设计、真实执行起点SHA、最终SHA及测试结果。修复Critical/Important并补对应回归测试，保留审查结论；审查前不称功能完成。
- [ ] **Step 4: 文档与运行验收**：README说明SEC_CONTACT、启用开关、两个季度/90天、显式映射、披露延迟、修订和只读范围。用SQLite备份API备份原DB，停止已确认属于本任务的服务后从当前工作树重新启动，沿用原 DB/config/.env；核对旧新闻摘要不变、认证后的X/SEC API及/smart-money可用。Token与SEC_CONTACT未配置时报告真实采集待配置，不能用fixtures证明真实最新数据；不得自动发送通知。
- [ ] **Step 5: 提交交付记录**：`git add README.md docs/superpowers/reports/2026-10-12-smart-money-verification.md`；`git commit -m "docs: document SEC smart money validation and local operation"`；最终给用户运行地址、已验证行为及实际配置限制。

## 依据与自审

SEC API路径、recent与补充分页结构依据 [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)；单位/修订依据 [13F FAQ](https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f)；交易代码依据 [Form 4](https://www.sec.gov/file/form4pdf)。夹具结构同时核对 [SEC XML Technical Specifications](https://www.sec.gov/submit-filings/technical-specifications) 的现行13F/Ownership版本；只读取本功能字段，允许其他可选字段。实施应从manifest发现原件，不能把文档例子固定为账户实际申报。

范围→任务：配置/迁移1，13F与修订2，Form4语义3，固定主机/限速4，覆盖/队列/取消/热配置5，认证/归档读取/分页6，导航/侧栏摘要/设置7，浏览器/全套测试/单次独立审查/原服务重启8。五项Review Focus均具名；实现签名使用统一数据契约。聪明钱任务不进入新闻/AI/通知链路；真采集验收必须单独标明本机凭据状态。
