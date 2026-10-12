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
 const app=await workspace({url:'http://radar.test/news',newsItems:[row]});try{
 assert.equal(app.document.querySelector('.x-original-text').textContent,original);assert.equal(app.document.querySelectorAll('.x-original-text img,.x-original-text script,.x-author script').length,0);assert.match(app.document.getElementById('newsList').textContent,/已编辑|引用作者/);
 app.document.getElementById('newsSearch').value='末尾独特关键词';app.document.getElementById('newsSearch').dispatchEvent(new app.window.Event('input'));assert.equal(app.document.querySelectorAll('.news-card').length,1);assert.deepEqual(app.errors,[]);
 }finally{app.window.close();}
});
test('X account status includes success and permission errors',async()=>{
 const app=await workspace({url:'http://radar.test/sources',onRequest:(path)=>path==='/api/status'?{ok:true,json:async()=>({source_health:[],x_accounts:[{configured_username:'Example',enabled:true,user:{id:'42',username:'Renamed',name:'Author'},last_success_at:1700000000,retry_at:1800000000,error:'权限不足'}]})}:null});try{assert.match(app.document.getElementById('sourcesList').textContent,/权限不足/);assert.match(app.document.getElementById('sourcesList').textContent,/最近成功/);}finally{app.window.close();}
});
