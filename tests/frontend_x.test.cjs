const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspace,tick}=require('./frontend_helpers.cjs');
test('X account settings preserve secrets and submit both modes',async()=>{
 const app=await workspace();try{
 assert.equal(app.document.getElementById('setting_x_bearer_token').value,'');
 app.document.getElementById('addXAccountBtn').click();app.document.getElementById('addXAccountBtn').click();
 const rows=app.document.querySelectorAll('.x-account-row');rows[0].querySelector('[data-x="username"]').value='Example';rows[0].querySelector('[data-x="push_mode"]').value='all';rows[1].querySelector('[data-x="username"]').value='Second';
 const p=app.window.settingsPayload();assert.equal(p.values.x_accounts[0].push_mode,'all');assert.equal(p.values.x_accounts[1].push_mode,'filtered');assert.equal(p.values.x_accounts[1].include_replies,false);assert.equal(p.secrets.x_bearer_token,'');
 app.document.getElementById('clear_x_bearer_token').checked=true;assert.ok(app.window.settingsPayload().clear_secrets.includes('x_bearer_token'));assert.match(app.document.getElementById('xPricingNotice').textContent,/收费/);
 }finally{app.window.close();}
});
test('X speech shows full text references edits safely and is searchable',async()=>{
 const original='<img src=x onerror=alert(1)>&amp;'+'a'.repeat(8000)+'末尾独特关键词';
 const row={source_type:'x',source:'X 用户言论',title:'X 用户言论 · @Example',summary:'短摘录',raw_text:original,url:'https://x.com/Example/status/101',priority:'info',related_symbols:'[]',edited_at:'2026-10-12T00:00:00Z',x_metadata:JSON.stringify({author_id:'42',username:'Example',author_name:'<script>bad</script>',references:[{type:'quoted',post_id:'9',username:'Original',author_id:'8'}]})};
 const app=await workspace({url:'http://radar.test/news',xItems:[row]});try{
 assert.equal(app.document.querySelector('.x-original-text').textContent,original);assert.equal(app.document.querySelectorAll('.x-original-text img,.x-original-text script,.x-author script').length,0);assert.match(app.document.getElementById('xPostList').textContent,/已编辑|引用作者/);
 app.document.getElementById('xPostSearch').value='末尾独特关键词';app.document.getElementById('xPostSearch').dispatchEvent(new app.window.Event('input'));assert.equal(app.document.querySelectorAll('#xPostList .news-card').length,1);assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});

const speech={source_type:'x',raw_text:'<img src=x onerror=alert(1)>'+'原文'.repeat(4000),summary:'摘录',
 x_metadata:{author_id:'42',author_name:'<script>作者</script>',username:'aleabitoreddit',post_id:'101'},url:'https://x.com/aleabitoreddit/status/101'};
const json=value=>({ok:true,status:200,json:async()=>value});
test('long speech is escaped and preview requires a click',async()=>{
 let previewCalls=0;const app=await workspace({url:'http://radar.test/news',xItems:[speech],onRequest:path=>{if(path==='/api/x/preview')previewCalls++;}});
 try{assert.equal(app.document.querySelector('#newsList .x-post'),null);assert.ok(app.document.querySelector('#xPostList').textContent.includes(speech.raw_text));assert.equal(app.document.querySelector('#xPostList img'),null);assert.equal(previewCalls,0);assert.equal(app.document.querySelector('#xPreviewUsername').value,'aleabitoreddit');assert.deepEqual(app.errors,[]);}finally{app.window.close();}
});
test('X loads while market/news are pending',async()=>{
 const app=await workspace({url:'http://radar.test/',xItems:[speech],onRequest:path=>path.startsWith('/api/news')?new Promise(()=>{}):null});
 try{assert.ok(app.document.querySelector('#xPostList').textContent.includes('aleabitoreddit'));assert.deepEqual(app.errors,[]);}finally{app.window.close();}
});
test('settings hide the sidebar and history navigation restores it',async()=>{
 const app=await workspace();try{assert.equal(app.document.querySelector('#researchWorkspace').hidden,true);app.document.querySelector('[data-section="newsPanel"]').click();await tick();assert.equal(app.document.querySelector('#researchWorkspace').hidden,false);app.window.history.back();await tick();await tick();assert.equal(app.document.querySelector('#researchWorkspace').hidden,true);assert.deepEqual(app.errors,[]);}finally{app.window.close();}
});
test('preview shows attribution and readable setup/rate-limit errors',async()=>{
 let mode='setup';const app=await workspace({url:'http://radar.test/news',onRequest:path=>path==='/api/x/preview'?json(mode==='setup'?{state:'not_configured',items:[],detail:'请配置 X Token'}:mode==='rate'?{state:'error',items:[],kind:'rate_limit',detail:'限流，请稍后重试'}:{state:'ok',items:[speech]}):null});
 try{app.document.querySelector('#xPreviewBtn').click();await tick();assert.match(app.document.querySelector('#xPreviewStatus').textContent,/Token/);mode='ok';app.document.querySelector('#xPreviewBtn').click();await tick();assert.match(app.document.querySelector('#xPreviewStatus').textContent,/未归档/);assert.match(app.document.querySelector('#xPreviewList').textContent,/ID 42/);assert.equal(app.document.querySelector('#xPreviewList img'),null);mode='rate';app.document.querySelector('#xPreviewBtn').click();await tick();assert.match(app.document.querySelector('#xPreviewStatus').textContent,/限流/);assert.match(app.document.querySelector('#xPricingHint').textContent,/收费/);assert.deepEqual(app.errors,[]);}finally{app.window.close();}
});
test('old X responses cannot replace current view',async()=>{
 let finishArchive,finishPreview;const accounts=[{id:'1',username:'A',name:'A'},{id:'2',username:'B',name:'B'}];
 const app=await workspace({url:'http://radar.test/news',xAccounts:accounts,onRequest:path=>{
  if(path.includes('/api/x/posts')&&path.includes('username=A'))return new Promise(resolve=>finishArchive=()=>resolve(json({items:[{...speech,raw_text:'STALE ARCHIVE'}],accounts})));
  if(path==='/api/x/preview')return new Promise(resolve=>finishPreview=()=>resolve(json({state:'ok',items:[{...speech,raw_text:'STALE PREVIEW'}]})));
  if(path==='/expired')return{ok:false,status:401,json:async()=>({detail:'登录失效'})};
 }});
 try{const select=app.document.querySelector('#xAccountSelect');select.value='A';select.dispatchEvent(new app.window.Event('change'));app.document.querySelector('#xPreviewBtn').click();await tick();select.value='B';select.dispatchEvent(new app.window.Event('change'));await tick();finishArchive();finishPreview();await tick();assert.doesNotMatch(app.document.querySelector('#xSidebar').textContent,/STALE/);
 select.value='A';select.dispatchEvent(new app.window.Event('change'));await tick();app.document.querySelector('[data-section="settingsSection"]').click();finishArchive();await tick();assert.doesNotMatch(app.document.querySelector('#xSidebar').textContent,/STALE/);
 app.document.querySelector('[data-section="newsPanel"]').click();await tick();await app.window.api('/expired').catch(()=>{});finishArchive();await tick();assert.doesNotMatch(app.document.querySelector('#xSidebar').textContent,/STALE/);
 }finally{app.window.close();}
});
test('X account status includes success and permission errors',async()=>{
 const app=await workspace({url:'http://radar.test/sources',onRequest:(path)=>path==='/api/status'?{ok:true,json:async()=>({source_health:[],x_accounts:[{configured_username:'Example',enabled:true,user:{id:'42',username:'Renamed',name:'Author'},last_success_at:1700000000,retry_at:1800000000,error:'权限不足'}]})}:null});try{assert.match(app.document.getElementById('sourcesList').textContent,/权限不足/);assert.match(app.document.getElementById('sourcesList').textContent,/最近成功/);}finally{app.window.close();}
});
