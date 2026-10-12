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

test('unknown and invalid-at-reception quotes never become live prices',async()=>{
 const app=await workspace({url:'http://radar.test/'});try{
  app.window.eval("state.session='all';");
  const now=Date.now()/1000,base={symbol:'NVDA',currency:'USD',price:999,source:'Longbridge',trade_status:'normal',quote_at:now,received_at:now};
  for(const quote of [{...base,session:'unknown'},{...base,session:'pre',received_at:now-60},{...base,session:'pre',received_at:null},{...base,session:'pre',price:0}]){
   app.window.renderSessionQuotes([quote],{market:'US',currency:'USD'});
   assert.equal(app.document.getElementById('stockPrice').textContent,'—');
   assert.equal(app.document.querySelectorAll('.session-card.fresh').length,0);
  }
  app.window.renderSessionQuotes([{...base,session:'pre',price:123}],{market:'US',currency:'USD'});
  assert.equal(app.document.getElementById('stockPrice').textContent,'$123.00');
 }finally{app.window.close();}
});

test('overview pre post pre ignores superseded success errors and finalization',async()=>{
 const pending=[];
 const app=await workspace({url:'http://radar.test/',onRequest:path=>path.startsWith('/api/market/overview')?new Promise(resolve=>pending.push(resolve)):null});
 try{
  app.window.eval("state.session='pre'; loadOverview(); state.session='post'; loadOverview(); state.session='pre'; loadOverview();");await tick();
  const response=price=>({ok:true,json:async()=>({items:[{symbol:'NVDA',price,currency:'USD',change_pct:price/100,error:null}]})});
  pending.at(-1)(response(200));await tick();
  assert.match(app.document.getElementById('watchlist').textContent,/\$200\.00/);
  pending[1](response(100));await tick();
  assert.match(app.document.getElementById('watchlist').textContent,/\$200\.00/);
  app.window.loadOverview();await tick();
  pending[0]({ok:false,status:503,json:async()=>({detail:'superseded failure'})});await tick();
  assert.equal(app.window.eval('state.overviewLoading'),true);
  assert.ok(!app.document.getElementById('toast').textContent.includes('superseded failure'));
  pending.at(-1)(response(300));await tick();
  assert.match(app.document.getElementById('watchlist').textContent,/\$300\.00/);
 }finally{app.window.close();}
});

test('overlapping quote requests keep the most recent response',async()=>{
 let capture=false;const pending=[];
 const app=await workspace({url:'http://radar.test/',onRequest:path=>capture&&path==='/api/quotes'?new Promise(resolve=>pending.push(resolve)):null});
 try{
  capture=true;app.window.loadQuotes();app.window.loadQuotes();await tick();
  const response=price=>({ok:true,json:async()=>({sessions:[{symbol:'NVDA',currency:'USD',session:'regular',source:'Longbridge',price,quote_at:Date.now()/1000,received_at:Date.now()/1000,trade_status:'normal'}]})});
  pending[1](response(200));await tick();pending[0](response(100));await tick();
  assert.equal(app.document.getElementById('stockPrice').textContent,'$200.00');
  assert.equal(app.window.eval('state.quotes[0].price'),200);
 }finally{app.window.close();}
});
