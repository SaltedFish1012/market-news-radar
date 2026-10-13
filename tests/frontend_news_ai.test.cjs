const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspace}=require('./frontend_helpers.cjs');
const json=value=>({ok:true,status:200,json:async()=>value});
const analysis={sentiment:'mixed',affected_symbols:['NVDA','005930.KS'],evidence_level:'unverified',
 rationale:'供应链可能受影响 <img src=x onerror=alert(1)>',uncertainty:'需核对原文 <script>alert(2)</script>'};
const news=(id,extra={})=>({id,source_type:'news',source:'Example',title:'新闻 '+id,summary:'原始摘要',
 url:'https://example.com/'+id,priority:'high',source_symbols:'["NVDA"]',related_symbols:'["NVDA"]',
 published_at:'2026-10-13T08:00:00Z',received_at:'2026-10-13T08:01:00Z',status:'received',ai_json:'',...extra});
const status=enabled=>json({configured:{ai:enabled},source_health:[{source:'ai',state:enabled?'ready':'disabled',detail:''}]});

test('news cards show full escaped AI results alongside the original news',async()=>{
 const app=await workspace({url:'http://radar.test/news',newsItems:[news(1,{status:'analyzed',ai_json:JSON.stringify(analysis)})],
  onRequest:path=>path==='/api/status'?status(true):undefined});
 try{
  const panel=app.document.querySelector('#newsList .news-ai-analysis');assert.ok(panel);
  assert.match(panel.textContent,/AI 初步分析/);assert.match(panel.textContent,/影响混合/);
  assert.match(panel.textContent,/尚未核实/);assert.match(panel.textContent,/005930.KS/);
  assert.ok(panel.textContent.includes(analysis.rationale));assert.ok(panel.textContent.includes(analysis.uncertainty));
  assert.equal(panel.querySelectorAll('img,script').length,0);
  assert.match(app.document.querySelector('#newsList .news-card').textContent,/原始摘要/);
  assert.match(app.document.getElementById('newsAiSummary').textContent,/已启用/);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/已分析 1 条/);
  assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

test('missing and malformed results remain visible without promising queued historical analysis',async()=>{
 const rows=[news(1),news(2,{status:'analyzed',ai_json:'{broken'}),news(3,{status:'analyzed',ai_json:'[]'})];
 const app=await workspace({url:'http://radar.test/news',newsItems:rows,onRequest:path=>path==='/api/status'?status(false):undefined});
 try{
  const panels=app.document.querySelectorAll('#newsList .news-ai-analysis');assert.equal(panels.length,3);
  assert.match(panels[0].textContent,/暂无分析/);assert.match(panels[1].textContent,/结果暂不可用/);
  assert.match(panels[2].textContent,/结果暂不可用/);
  const summary=app.document.getElementById('newsAiSummary');assert.match(summary.textContent,/未启用/);
  assert.match(summary.textContent,/历史新闻不会自动补分析/);assert.doesNotMatch(summary.textContent,/排队|正在分析/);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/已分析 0 条/);
  assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

test('analysis counts follow news filters and completed results appear on refresh',async()=>{
 let rows=[news(1),news(2,{priority:'info',related_symbols:'["MU"]',source_symbols:'["MU"]'})];
 const app=await workspace({url:'http://radar.test/news',onRequest:path=>{
  if(path.startsWith('/api/news'))return json({items:rows});if(path==='/api/status')return status(true);
 }});
 try{
  const filter=app.document.getElementById('newsPriority');filter.value='high';filter.dispatchEvent(new app.window.Event('change'));
  rows=[news(1,{status:'analyzed',ai_json:JSON.stringify(analysis)}),rows[1]];
  await app.window.loadWorkspace();
  assert.equal(app.document.querySelectorAll('#newsList .news-card').length,1);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/已分析 1 条/);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/暂无有效分析 0 条/);
  assert.ok(app.document.querySelector('#newsList .news-ai-analysis').textContent.includes(analysis.rationale));
  filter.value='info';filter.dispatchEvent(new app.window.Event('change'));
  assert.match(app.document.getElementById('newsAiCounts').textContent,/已分析 0 条/);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/暂无有效分析 1 条/);
  assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

test('malformed enum objects cannot abort rendering other news or count as valid analysis',async()=>{
 const broken=[{sentiment:{toString:0}},{evidence_level:{toString:0}},{sentiment:['mixed']},{evidence_level:['unverified']}];
 const rows=broken.map((extra,i)=>news(i+1,{status:'analyzed',ai_json:JSON.stringify({...analysis,...extra})}));
 rows.push(news(5,{status:'analyzed',ai_json:JSON.stringify(analysis)}));
 const app=await workspace({url:'http://radar.test/news',newsItems:rows});
 try{
  assert.equal(app.document.querySelectorAll('#newsList .news-card').length,5);
  assert.equal(app.document.querySelectorAll('#newsList .news-ai-analysis.empty').length,4);
  assert.match(app.document.getElementById('newsAiCounts').textContent,/已分析 1 条/);
  assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});
