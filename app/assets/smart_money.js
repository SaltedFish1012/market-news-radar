'use strict';
const smartMoneyState={tab:'holdings',cik:'',period:'',symbol:'',code:'',derivative:'',offset:0,overview:null,
 overviewGeneration:0,detailsGeneration:0,limit:20};
const smartChange={new:'新建',increase:'增加',decrease:'减少',exit:'退出',unchanged:'未变',unknown:'未知'};
const smartKinds={P:'买入（P）',S:'卖出（S）',A:'授予（A）',M:'行权（M）',F:'扣股缴税（F）',G:'赠与（G）'};
const smartComparison={compared:'可比较 · 报告期持仓差异',baseline_missing:'缺少上季基准 · 变化未知',not_comparable:'无法比较 · 披露不完整或修订待解释'};
function invalidateSmartMoneyRequests(){smartMoneyState.overviewGeneration++;smartMoneyState.detailsGeneration++;}
function smartCurrent(generation,type){return state.loggedIn&&generation===smartMoneyState[type+'Generation']&&(type!=='details'||activePage==='smartMoney');}
function secSourceLink(url,label='SEC 原件 ↗'){
 const safe=safeUrl(url);if(!safe)return '';const parsed=new URL(safe);
 return parsed.protocol==='https:'&&parsed.hostname==='www.sec.gov'&&parsed.pathname.startsWith('/Archives/')?`<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:'';
}
function smartDecimal(value){return value==null?'未知':esc(value);}
function smartMoneyStatus(){
 const overview=smartMoneyState.overview;if(!overview)return '正在读取本地披露归档…';
 const config=overview.configured||{};const messages=[];
 if(!config.enabled)messages.push('采集未启用，仍可查阅已归档披露。');
 if(!config.contact_configured)messages.push('尚未配置 SEC 联系邮箱，请前往设置填写。');
 for(const source of overview.sources||[]){
  const coverage={complete:'覆盖完整',partial:'覆盖不完整',pending:'等待首次采集'}[source.coverage_state]||source.coverage_state||'等待采集';
  messages.push(`${source.name||source.source_key}：${coverage}，待处理 ${source.pending_count||0} 份${source.last_error?'；错误 '+source.last_error:''}${source.retry_at?'；重试 '+dateTime(source.retry_at,true):''}${source.last_success_at?'；最近成功 '+dateTime(source.last_success_at,true):''}`);
 }
 for(const item of overview.scope?.unsupported||[])messages.push(`${item.symbol}：${item.reason}`);
 return messages.join('\n')||'暂无配置来源。请在设置中添加机构或美股自选标的。';
}
function renderSmartMoneyOverview(){
 const overview=smartMoneyState.overview;if(!overview)return;
 const summary=$('smartMoneySummary');summary.hidden=!['market','news'].includes(activePage);
 const counts=overview.counts||{};
 summary.innerHTML=`<div class="panel-title"><h2>聪明钱</h2><a href="/smart-money" data-smart-open>查看全部 ↗</a></div><p class="small-muted">SEC 披露 · 机构持仓 ${esc(counts.holdings||0)} 行 / 内部人交易 ${esc(counts.insiders||0)} 行</p><p class="smart-status">${esc(smartMoneyStatus())}</p>${(overview.recent_filings||[]).slice(0,3).map(f=>`<div class="smart-summary-filing"><strong>${esc(f.entity_name)}</strong><small>${esc(f.form)} · 披露日 ${esc(f.filed_at)}</small>${secSourceLink(f.source_url)}</div>`).join('')}`;
 if(activePage==='smartMoney'){$('smartMoneyStatus').textContent=smartMoneyStatus();renderSmartFilters();}
}
async function loadSmartMoneyOverview(){
 if(!state.loggedIn||activePage==='settings')return;
 const generation=++smartMoneyState.overviewGeneration;
 try{const result=await api('/api/smart-money/overview');if(!smartCurrent(generation,'overview'))return;smartMoneyState.overview=result;renderSmartMoneyOverview();}
 catch(error){if(smartCurrent(generation,'overview')){$('smartMoneyStatus').textContent='归档状态读取失败：'+error.message;if(['market','news'].includes(activePage))$('smartMoneySummary').textContent='聪明钱状态读取失败：'+error.message;}}
}
function renderSmartFilters(){
 const s=smartMoneyState;const institutions=(s.overview?.sources||[]).filter(p=>p.source_key.startsWith('institution:'));
 $('smartMoneyFilters').innerHTML=`${s.tab==='holdings'?`<label>机构<select id="smartCik"><option value="">全部机构</option>${institutions.map(p=>`<option value="${esc(p.source_key.split(':')[1])}">${esc(p.name)} · ${esc(p.source_key.split(':')[1])}</option>`).join('')}</select></label><label>报告期<input id="smartPeriod" type="date" aria-label="报告期季度末"></label>`:''}<label>标的代码<input id="smartSymbol" placeholder="全部 / NVDA" maxlength="15"></label>${s.tab==='insiders'?'<label>交易类别<select id="smartCode"><option value="">全部类别</option>'+Object.entries(smartKinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')+[...'BCDEHIJKLOQRTUVWXYZ'].map(k=>`<option value="${k}">其他（${k}）</option>`).join('')+'</select></label><label>证券类型<select id="smartDerivative"><option value="">全部类型</option><option value="false">非衍生证券</option><option value="true">衍生证券</option></select></label>':''}<button type="button" class="button subtle" data-smart-reset>清除筛选</button>`;
 for(const [id,key] of [['smartCik','cik'],['smartPeriod','period'],['smartSymbol','symbol'],['smartCode','code'],['smartDerivative','derivative']])if($(id))$(id).value=s[key];
 document.querySelectorAll('[data-smart-tab]').forEach(b=>{const selected=b.dataset.smartTab===s.tab;b.classList.toggle('active',selected);b.setAttribute('aria-selected',String(selected));});
}
function smartFilingInfo(f){
 const amendment={new_holdings:'新增持仓修订',restatement:'重报修订',unknown:'修订待解释'}[f.amendment_type];
 return `<p class="small-muted">${esc(f.form)}${amendment?' · '+esc(amendment):''} · 披露日 ${esc(f.filed_at)} · ${secSourceLink(f.source_url)}${f.completeness==='unsupported'?' · 旧单位不支持':''}</p>`;
}
function renderHoldings(data){
 const reports=(data.reports||[]).map(r=>`<article class="smart-report"><h3>${esc(r.entity_name)} · CIK ${esc(r.cik)}</h3><p>报告期 ${esc(r.period)}${r.previous_period?' · 上期 '+esc(r.previous_period):''}</p><p class="badge">${esc(smartComparison[r.comparison_state]||r.comparison_state)}</p>${r.detail?`<p>${esc(r.detail)}</p>`:''}${(r.filings||[]).map(smartFilingInfo).join('')}</article>`).join('');
 const rows=(data.items||[]).map(r=>`<article class="smart-row"><div class="smart-row-head"><h3>${esc(r.issuer_name)}</h3><span class="badge">${esc(smartChange[r.change]||r.change)}</span></div><p>${r.symbol?esc(r.symbol):'未映射 · CUSIP '+esc(r.cusip)} · ${esc(r.security_class)}${r.put_call?' · '+esc(r.put_call):''}</p><p class="small-muted">${esc(r.entity_name)} · 报告期 ${esc(r.report_period)}</p><dl class="smart-values"><div><dt>${r.share_type==='PRN'?'本金（PRN）':'股数（SH）'}</dt><dd>${smartDecimal(r.shares)}</dd></div><div><dt>披露市值 · USD</dt><dd>${smartDecimal(r.value_usd)}</dd></div><div><dt>报告期数量差异</dt><dd>${smartDecimal(r.shares_delta)}</dd></div></dl></article>`).join('');
 return reports+rows||'<div class="empty-state">暂无匹配持仓归档。配置机构后等待首次采集。</div>';
}
function renderInsiders(data){
 return (data.items||[]).map(r=>{
  const owners=(r.owners||[]).map(o=>{const roles=[o.is_director?'董事':'',o.is_officer?'高管'+(o.officer_title?' · '+o.officer_title:''):'',o.is_ten_percent_owner?'10% 股东':'',o.is_other?'其他身份'+(o.other_text?' · '+o.other_text:''):''].filter(Boolean);return `<p>${esc(o.name)} · CIK ${esc(o.cik)}${roles.length?' · '+esc(roles.join(' / ')):''}</p>`;}).join('');
  const notes=Object.entries(r.footnotes||{}).map(([id,text])=>`<p><strong>${esc(id)}</strong> ${esc(text)}</p>`).join('');
  return `<article class="smart-row"><div class="smart-row-head"><h3>${esc(r.issuer_name)}</h3><span class="badge">${esc(smartKinds[r.code]||'其他（'+r.code+'）')}</span></div><p>${esc((r.symbols||[]).join(' / ')||'尚无标的映射')} · 发行人 CIK ${esc(r.issuer_cik)}</p><p>${r.derivative?'衍生证券':'非衍生证券'} · ${esc(r.security_title)} · ${r.amended?'修订披露（4/A）':'Form 4'}${!r.derivative&&['P','S'].includes(r.code)&&!r.amended?' · 公开市场或私下买卖':''}</p><p>交易日 ${esc(r.transaction_date||'未知')} · 披露日 ${esc(r.filed_at)}</p>${owners}<dl class="smart-values"><div><dt>数量 / 单价 USD</dt><dd>${smartDecimal(r.shares)} / ${smartDecimal(r.price)}</dd></div><div><dt>金额 USD</dt><dd>${r.amount_usd==null?'金额未知':smartDecimal(r.amount_usd)}</dd></div><div><dt>交易后数量</dt><dd>${smartDecimal(r.post_shares)}</dd></div></dl><p class="small-muted">${r.acquired_disposed==='A'?'取得':r.acquired_disposed==='D'?'处置':'取得/处置未知'} · ${r.ownership==='D'?'直接持有':r.ownership==='I'?'间接持有':'持有方式未知'}${r.ownership_nature?' · '+esc(r.ownership_nature):''} · 10b5-1 ${r.ten_b5_1==null?'未披露':r.ten_b5_1?'已标记':'未标记'}</p>${secSourceLink(r.source_url)}${notes?`<details class="smart-footnotes"><summary>披露足注</summary>${notes}</details>`:''}</article>`;
 }).join('')||'<div class="empty-state">暂无匹配内部人交易归档。仅支持已确认发行人 CIK 的美股股票。</div>';
}
async function loadSmartMoneyDetails(){
 if(!state.loggedIn||activePage!=='smartMoney')return;
 const s=smartMoneyState,generation=++s.detailsGeneration,tab=s.tab;
 const query=new URLSearchParams({limit:String(s.limit),offset:String(s.offset)});
 for(const key of tab==='holdings'?['cik','period','symbol']:['symbol','code','derivative'])if(s[key])query.set(key,s[key]);
 $('smartMoneyStatus').textContent=smartMoneyStatus()+'\n正在读取本地归档…';
 try{let data=await api('/api/smart-money/'+tab+'?'+query);if(!smartCurrent(generation,'details'))return;
  $('smartMoneyStatus').textContent=smartMoneyStatus();
  $('smartMoneyList').innerHTML=tab==='holdings'?renderHoldings(data):renderInsiders(data);
  $('smartMoneyPagination').innerHTML=`<button type="button" class="button subtle" data-smart-prev ${s.offset===0?'disabled':''}>上一页</button><span>共 ${esc(data.total)} 行 · 第 ${Math.floor(s.offset/s.limit)+1} 页</span><button type="button" class="button subtle" data-smart-next ${s.offset+s.limit>=data.total?'disabled':''}>下一页</button>`;
 }catch(error){if(smartCurrent(generation,'details'))$('smartMoneyStatus').textContent=smartMoneyStatus()+'\n归档读取失败：'+error.message;}
}
function bindSmartMoney(){
 $('smartMoneyTabs').addEventListener('click',event=>{const button=event.target.closest('[data-smart-tab]');if(!button)return;smartMoneyState.tab=button.dataset.smartTab;smartMoneyState.offset=0;renderSmartFilters();loadSmartMoneyDetails();});
 $('smartMoneyFilters').addEventListener('change',event=>{const keys={smartCik:'cik',smartPeriod:'period',smartSymbol:'symbol',smartCode:'code',smartDerivative:'derivative'};const key=keys[event.target.id];if(!key)return;smartMoneyState[key]=event.target.value.trim();smartMoneyState.offset=0;loadSmartMoneyDetails();});
 $('smartMoneyFilters').addEventListener('click',event=>{if(event.target.closest('[data-smart-reset]')){Object.assign(smartMoneyState,{cik:'',period:'',symbol:'',code:'',derivative:'',offset:0});renderSmartFilters();loadSmartMoneyDetails();}});
 $('smartMoneyPagination').addEventListener('click',event=>{const previous=event.target.closest('[data-smart-prev]'),next=event.target.closest('[data-smart-next]');if(!previous&&!next)return;smartMoneyState.offset=Math.max(0,smartMoneyState.offset+(next?1:-1)*smartMoneyState.limit);loadSmartMoneyDetails();});
 $('smartMoneySummary').addEventListener('click',event=>{if(event.target.closest('[data-smart-open]')){event.preventDefault();navigatePage('smartMoney');}});
 $('addInstitutionBtn').addEventListener('click',()=>{appendInstitution();settingsDirty();});
 $('exampleInstitutionBtn').addEventListener('click',()=>{if(![...$('settingsInstitutions').querySelectorAll('[data-institution="cik"]')].some(el=>el.value==='1067983'))appendInstitution({name:'Berkshire Hathaway',cik:'1067983',enabled:true});settingsDirty();});
 $('settingsInstitutions').addEventListener('click',event=>{const button=event.target.closest('[data-remove-institution]');if(button){button.parentElement.remove();settingsDirty();}});
}
function appendInstitution(institution={name:'',cik:'',enabled:true}){
 const row=document.createElement('div');row.className='institution-row';row.innerHTML=`<label>机构名称<input data-institution="name" maxlength="150" value="${esc(institution.name)}"></label><label>CIK<input data-institution="cik" inputmode="numeric" maxlength="10" value="${esc(institution.cik)}"></label><label class="switch-label"><input type="checkbox" data-institution="enabled" ${institution.enabled?'checked':''}>启用机构</label><button type="button" class="button subtle" data-remove-institution>删除机构</button>`;$('settingsInstitutions').appendChild(row);
}
function fillSmartMoneySettings(snapshot){
 $('settingsInstitutions').replaceChildren();(snapshot.values.smart_money_institutions||[]).forEach(appendInstitution);
 for(const key of ['smart_money_cusip_map','smart_money_issuer_map'])$('setting_'+key).value=JSON.stringify(snapshot.values[key]||{},null,2);
}
function smartMoneySettingsPayload(values){
 values.smart_money_institutions=[...$('settingsInstitutions').children].map(row=>{const read=key=>row.querySelector(`[data-institution="${key}"]`);return{name:read('name').value.trim(),cik:read('cik').value.trim(),enabled:read('enabled').checked};});
 for(const key of ['smart_money_cusip_map','smart_money_issuer_map']){let mapping;try{mapping=JSON.parse($('setting_'+key).value);}catch{throw new Error(key+' 须为有效 JSON 对象');}if(!mapping||Array.isArray(mapping)||typeof mapping!=='object')throw new Error(key+' 须为 JSON 对象');values[key]=mapping;}
}
