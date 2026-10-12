const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspace,tick}=require('./frontend_helpers.cjs');
const json=value=>({ok:true,status:200,json:async()=>value});
const filing={entity_name:'<img src=x>Offline institution',accession:'0001067983-26-000001',form:'13F-HR',filed_at:'2026-08-14',report_period:'2026-06-30',source_url:'https://www.sec.gov/Archives/edgar/data/1067983/000106798326000001/primary_doc.xml',amendment_type:'none',completeness:'complete'};
const overview={configured:{enabled:false,contact_configured:false},sources:[{source_key:'institution:1067983',name:'Offline institution',coverage_state:'partial',pending_count:1,last_error:'invalid_filing'}],scope:{supported:[{cik:'1045810',symbols:['NVDA']}],unsupported:[]},recent_filings:[filing],counts:{filings:1,holdings:1,insiders:1}};
const holdings={reports:[{cik:'1067983',entity_name:filing.entity_name,period:'2026-06-30',previous_period:null,comparison_state:'baseline_missing',complete:true,filings:[filing],detail:''}],items:[{cik:'1067983',entity_name:filing.entity_name,report_period:'2026-06-30',issuer_name:'<script>issuer</script>',symbol:null,cusip:'67066G104',security_class:'COM',share_type:'SH',put_call:'',shares:'100',value_usd:'1250',change:'unknown',shares_delta:null}],total:21,limit:20,offset:0};
const insiders={items:[{issuer_name:'Offline issuer',issuer_cik:'1045810',symbols:['NVDA'],form:'4/A',amended:true,derivative:true,code:'M',kind:'exercise',transaction_date:'2026-08-12',filed_at:'2026-08-14',source_url:filing.source_url,security_title:'Option',shares:'10',price:null,amount_usd:null,post_shares:'20',ownership:'I',ownership_nature:'Trust',owners:[{name:'<img src=x>Owner',cik:'1',is_director:true,is_officer:true,officer_title:'CEO'}],footnotes:{F1:'<script>note</script>'},ten_b5_1:null}],total:1,limit:20,offset:0};
const smartMoneyData={overview,holdings,insiders};

test('smart money preserves disclosure dates and source attribution',async()=>{
 const app=await workspace({url:'http://radar.test/smart-money',smartMoneyData});try{
 const list=app.document.querySelector('#smartMoneyList');assert.match(list.textContent,/2026-06-30/);assert.match(list.textContent,/2026-08-14/);assert.match(list.textContent,/缺少上季基准/);assert.match(list.textContent,/未映射.*67066G104/);assert.ok(list.querySelector('a').href.startsWith('https://www.sec.gov/Archives/'));assert.equal(list.querySelector('img,script'),null);assert.match(app.document.querySelector('#smartMoneyStatus').textContent,/联系邮箱/);assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

test('amendments derivatives ownership footnotes and unknown prices are explicit',async()=>{
 const app=await workspace({url:'http://radar.test/smart-money',smartMoneyData});try{
 app.document.querySelector('[data-smart-tab="insiders"]').click();await tick();const list=app.document.querySelector('#smartMoneyList');assert.match(list.textContent,/行权.*M/);assert.match(list.textContent,/衍生证券/);assert.match(list.textContent,/修订披露/);assert.match(list.textContent,/金额未知/);assert.match(list.textContent,/间接持有/);assert.match(list.textContent,/CIK 1/);assert.match(list.textContent,/CEO/);assert.match(list.textContent,/2026-08-12/);assert.equal(list.querySelector('img,script'),null);assert.equal(list.querySelector('details').textContent.includes('<script>note</script>'),true);
 }finally{app.window.close();}
});

test('summary appears only on market and news and loads independently',async()=>{
 for(const path of ['/','/news','/smart-money','/settings']){const app=await workspace({url:'http://radar.test'+path,smartMoneyData,onRequest:p=>p.startsWith('/api/news')?new Promise(()=>{}):null});try{assert.equal(app.document.querySelector('#smartMoneySummary').hidden,!['/','/news'].includes(path));if(path==='/')assert.match(app.document.querySelector('#smartMoneySummary').textContent,/Offline institution/);assert.deepEqual(app.errors,[]);}finally{app.window.close();}}
});

test('stale details are discarded after tab filter page or logout and pagination resets',async()=>{
 let finish;const requests=[];let block=false;
 const app=await workspace({url:'http://radar.test/smart-money',smartMoneyData,onRequest:p=>{if(p.startsWith('/api/smart-money/holdings')){requests.push(p);if(block)return new Promise(resolve=>finish=()=>resolve(json({...holdings,items:[{...holdings.items[0],issuer_name:'STALE RESULT'}]})));}}});try{
 app.document.querySelector('[data-smart-next]').click();await tick();assert.match(requests.at(-1),/offset=20/);
 block=true;const symbol=app.document.querySelector('#smartSymbol');symbol.value='NVDA';symbol.dispatchEvent(new app.window.Event('change',{bubbles:true}));await tick();assert.match(requests.at(-1),/offset=0/);
 app.document.querySelector('[data-smart-tab="insiders"]').click();await tick();finish();await tick();assert.doesNotMatch(app.document.querySelector('#smartMoneyList').textContent,/STALE/);
 app.document.querySelector('[data-smart-tab="holdings"]').click();await tick();app.document.querySelector('[data-section="newsPanel"]').click();finish();await tick();assert.doesNotMatch(app.document.querySelector('#smartMoneyList').textContent,/STALE/);
 app.document.querySelector('[data-section="smartMoneySection"]').click();await tick();app.document.querySelector('#logoutBtn').click();finish();await tick();assert.doesNotMatch(app.document.querySelector('#smartMoneyList').textContent,/STALE/);
 }finally{app.window.close();}
});

test('all SEC transaction codes remain separate and unsupported reports keep their source',async()=>{
 const data={...smartMoneyData,holdings:{...holdings,reports:[{...holdings.reports[0],complete:false,comparison_state:'not_comparable',detail:'旧单位不支持',filings:[{...filing,completeness:'unsupported'}]}]},insiders:{...insiders,items:['P','S','A','M','F','G','J'].map(code=>({...insiders.items[0],code,derivative:false,amended:false,form:'4'}))}};
 const app=await workspace({url:'http://radar.test/smart-money',smartMoneyData:data});try{
 assert.match(app.document.querySelector('#smartMoneyList').textContent,/无法比较/);assert.match(app.document.querySelector('#smartMoneyList').textContent,/旧单位不支持/);assert.ok(app.document.querySelector('#smartMoneyList a'));
 app.document.querySelector('[data-smart-tab="insiders"]').click();await tick();const text=app.document.querySelector('#smartMoneyList').textContent;for(const label of ['买入（P）','卖出（S）','授予（A）','行权（M）','扣股缴税（F）','赠与（G）','其他（J）'])assert.ok(text.includes(label),label);assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

test('settings persist only intended SEC fields with click-only example and valid JSON',async()=>{
 const app=await workspace();try{
 assert.equal(app.document.querySelector('#setting_smart_money_enabled').checked,false);assert.equal(app.document.querySelector('#setting_smart_money_poll_interval').value,'900');assert.equal(app.document.querySelector('#settingsInstitutions').children.length,0);
 app.document.querySelector('#exampleInstitutionBtn').click();assert.equal(app.document.querySelector('[data-institution="cik"]').value,'1067983');app.document.querySelector('#addInstitutionBtn').click();app.document.querySelectorAll('[data-institution="name"]')[1].value='Second';app.document.querySelectorAll('[data-institution="cik"]')[1].value='123';app.document.querySelector('[data-remove-institution]').click();
 app.document.querySelector('#setting_smart_money_cusip_map').value='{"67066G104":"NVDA"}';const payload=app.window.settingsPayload();assert.equal(payload.values.smart_money_institutions.length,1);assert.equal(payload.values.smart_money_cusip_map['67066G104'],'NVDA');assert.equal(payload.secrets.x_bearer_token,'');
 app.document.querySelector('#setting_smart_money_issuer_map').value='broken';assert.throws(()=>app.window.settingsPayload(),/JSON/);assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});
