const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspace,tick}=require('./frontend_helpers.cjs');
test('four session cards show source time freshness and missing prices',async()=>{
 const now=Date.now()/1000;
 const app=await workspace({url:'http://radar.test/',quoteItems:[{symbol:'NVDA',price:123,currency:'USD',source:'Longbridge',session:'pre',quote_at:now,received_at:now,trade_status:'normal',fresh:true}]});
 try{assert.equal(app.document.querySelectorAll('[data-quote-session]').length,4);assert.match(app.document.getElementById('sessionQuotes').textContent,/暂无报价/);assert.match(app.document.getElementById('sessionQuotes').textContent,/Longbridge/);assert.deepEqual(app.errors,[]);}finally{app.window.close();}
});
test('session switch sends parameter and ignores late response',async()=>{
 const requests=[],pending=[];
 const app=await workspace({url:'http://radar.test/',onRequest:(path)=>{
   if(path.startsWith('/api/market/candles')){requests.push(path);return new Promise(resolve=>{pending.push(resolve);});}
 }});
 try{const select=app.document.getElementById('sessionSelect');select.value='overnight';select.dispatchEvent(new app.window.Event('change'));await tick();assert.equal(new URL(requests.at(-1),'http://radar.test').searchParams.get('session'),'overnight');assert.equal(app.document.getElementById('strategySession').value,'overnight');pending[0]({ok:true,json:async()=>({name:'LATE REGULAR'})});await tick();assert.notEqual(app.document.getElementById('stockName').textContent,'LATE REGULAR');}finally{app.window.close();}
});
test('instrument metadata and rule session survive settings payload',async()=>{
 const app=await workspace();try{
 app.document.getElementById('setting_instruments').value=JSON.stringify([{symbol:'NVDA',market:'US',currency:'USD',timezone:'America/New_York',asset_type:'stock'}]);
 app.document.getElementById('addRuleBtn').click();
 app.document.querySelectorAll('[data-rule-session]').forEach(el=>el.checked=el.value==='overnight');
 const payload=app.window.settingsPayload();assert.deepEqual(Array.from(payload.watchlist.price_rules[0].sessions),['overnight']);assert.equal(payload.watchlist.instruments[0].currency,'USD');
 assert.match(app.window.formatMoney(1000,'KRW'),/KRW|₩/);assert.ok(app.document.getElementById('setting_lb_overnight_enabled'));
 }finally{app.window.close();}
});

test('CSV follows instrument and quote expiry is visible',async()=>{
 const app=await workspace();try{
 let csv;app.window.Blob=class{constructor(parts){csv=parts.join('');}};app.window.URL.createObjectURL=()=> 'blob:test';app.window.URL.revokeObjectURL=()=>{};app.window.HTMLAnchorElement.prototype.click=()=>{};
 app.window.eval(`state.result={symbol:'7203.T',strategy:'ema_cross',interval:'1d',currency:'JPY',timezone:'Asia/Tokyo',session:'regular',instrument:{market:'JP'},trades:[{entry_time:1700000000,exit_time:1700100000,entry_price:1,exit_price:2,pnl:1,return_pct:100}]};`);
 app.window.exportTrades();assert.match(csv,/net_pnl,net_return_pct,currency,market,timezone,session/);assert.match(csv,/JPY,JP,Asia\/Tokyo,regular/);assert.ok(!csv.includes('net_pnl_usd'));
 app.window.renderSessionQuotes([{symbol:'NVDA',currency:'USD',price:123,session:'pre',quote_at:Date.now()/1000-130,received_at:Date.now()/1000-130,trade_status:'normal',source:'Longbridge'}],{market:'US',currency:'USD'});assert.match(app.document.getElementById('sessionQuotes').textContent,/已过期/);
 }finally{app.window.close();}
});
