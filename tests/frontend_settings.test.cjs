// npm install --prefix /tmp/radar-dom-tests jsdom --no-save --ignore-scripts
// NODE_PATH=/tmp/radar-dom-tests/node_modules node --test tests/frontend_settings.test.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {workspace,tick,initial,projectJSON}=require('./frontend_helpers.cjs');

test('Settings loads and remains usable while market requests never finish',async()=>{
    const app=await workspace();try{
        assert.deepEqual(app.errors,[]);
        assert.equal(app.document.getElementById('settingsView').hidden,false);
        assert.equal(app.document.getElementById('workspaceView').hidden,true);
        assert.equal(app.document.getElementById('authOverlay').hidden,true);
        assert.equal(app.document.getElementById('saveSettingsBtn').disabled,false);
        assert.ok(app.document.getElementById('setting_ai_provider').options.length===4);
        const provider=app.document.getElementById('setting_ai_provider');provider.value='gemini';provider.dispatchEvent(new app.window.Event('change',{bubbles:true}));
        assert.ok([...app.document.getElementById('setting_ai_model_preset').options].some(option=>option.value==='gemini-3.8-flash'));
        assert.equal(app.document.getElementById('setting_ai_base_url').value,initial.providers.gemini.base_url);
        app.document.querySelector('[data-settings-tab="data"]').click();
        assert.equal(app.document.querySelector('[data-settings-panel="data"]').hidden,false);
        assert.equal(app.document.querySelector('[data-settings-panel="ai"]').hidden,true);
        app.document.querySelector('[data-section="marketSection"]').click();
        assert.equal(app.document.getElementById('settingsView').hidden,true);
        app.document.querySelector('[data-section="settingsSection"]').click();
        assert.equal(app.document.getElementById('settingsView').hidden,false);
        assert.equal(app.window.location.pathname,'/settings');
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

for(const remember of [true,false])test(`Login form sends remember=${remember} and never stores the password`,async()=>{
    let submitted;
    const app=await workspace({authenticated:false,onLogin:body=>submitted=body});try{
        assert.equal(app.document.getElementById('authOverlay').hidden,false);
        assert.equal(app.document.getElementById('rememberLogin').checked,true);
        app.document.getElementById('rememberLogin').checked=remember;
        app.document.getElementById('loginToken').value='dummy-password-for-ui-test';
        app.document.getElementById('loginForm').dispatchEvent(new app.window.Event('submit',{bubbles:true,cancelable:true}));
        await tick();
        assert.equal(submitted.remember,remember);
        assert.equal(submitted.token,'dummy-password-for-ui-test');
        assert.equal(app.document.getElementById('loginToken').value,'');
        assert.equal(app.document.getElementById('authOverlay').hidden,true);
        assert.equal(app.window.localStorage.length,0);
        assert.equal(app.window.sessionStorage.length,0);
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('Settings hash link displays before market completion',async()=>{
    const app=await workspace({url:'http://radar.test/#settings'});try{
        assert.equal(app.document.getElementById('settingsView').hidden,false);
        assert.equal(app.document.getElementById('setting_symbols').value,initial.watchlist.symbols.join(', '));
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('Failed settings request shows an error and leaves Reload usable',async()=>{
    const app=await workspace({failed:true});try{
        assert.equal(app.document.getElementById('settingsView').hidden,false);
        assert.equal(app.document.getElementById('settingsMessage').hidden,false);
        assert.match(app.document.getElementById('settingsMessage').textContent,/配置读取暂不可用/);
        assert.equal(app.document.getElementById('reloadSettingsBtn').disabled,false);
        assert.equal(app.document.getElementById('saveSettingsBtn').disabled,true);
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

const pageCases=[['/','market','marketSection'],['/strategies','strategies','strategySection'],['/news','news','newsPanel'],['/smart-money','smartMoney','smartMoneySection'],['/alerts','alerts','alertsSection'],['/sources','sources','sourcesSection'],['/settings','settings','settingsSection']];
function visiblePage(document){const visible=[...document.querySelectorAll('[data-page]')].filter(page=>!page.hidden);assert.equal(visible.length,1);return visible[0].dataset.page;}
for(const[path,page,section]of pageCases)test(`Direct ${path} displays only ${page} without section scrolling`,async()=>{
    const app=await workspace({url:'http://radar.test'+path});try{
        assert.equal(visiblePage(app.document),page);
        assert.equal(app.document.querySelector('[aria-current="page"]').dataset.section,section);
        assert.equal(app.window.lastScroll.top,0);
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('Sidebar navigation creates history and back/forward restores the proper page',async()=>{
    const app=await workspace({url:'http://radar.test/'});try{
        for(const[path,page,section]of pageCases.slice(1)){
            app.document.querySelector(`[data-section="${section}"]`).click();
            assert.equal(app.window.location.pathname,path);
            assert.equal(visiblePage(app.document),page);
        }
        app.window.history.back();await tick();await tick();
        assert.equal(app.window.location.pathname,'/sources');assert.equal(visiblePage(app.document),'sources');
        app.window.history.forward();await tick();await tick();
        assert.equal(app.window.location.pathname,'/settings');assert.equal(visiblePage(app.document),'settings');
        app.document.getElementById('setting_ai_model_custom').value='saved-ui-draft';
        app.document.querySelector('[data-section="newsPanel"]').click();
        app.document.querySelector('[data-section="settingsSection"]').click();
        assert.equal(app.document.getElementById('setting_ai_model_custom').value,'saved-ui-draft');
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('Strategy page loads the chosen symbol, interval and provider without using the market page',async()=>{
    const calls=[];const app=await workspace({url:'http://radar.test/strategies',onMarket:path=>calls.push(path)});try{
        app.document.getElementById('strategySymbolInput').value='AAPL';
        app.document.getElementById('strategyInterval').value='1h';
        app.document.getElementById('strategyProvider').value='demo';
        app.document.getElementById('researchContextForm').dispatchEvent(new app.window.Event('submit',{bubbles:true,cancelable:true}));
        const request=new URL(calls.filter(path=>path.startsWith('/api/market/candles')).at(-1),'http://radar.test');
        assert.equal(request.searchParams.get('symbol'),'AAPL');
        assert.equal(request.searchParams.get('interval'),'1h');
        assert.equal(request.searchParams.get('provider'),'demo');
        assert.equal(visiblePage(app.document),'strategies');
        assert.equal(app.document.getElementById('providerSelect').value,'demo');
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('News page filters by its own selected stock and does not start market requests',async()=>{
    const calls=[];const newsItems=[
        {title:'NVDA news',source:'fixture',related_symbols:'["NVDA"]',priority:'info'},
        {title:'MU news',source:'fixture',related_symbols:'["MU"]',priority:'info'}
    ];const app=await workspace({url:'http://radar.test/news',onMarket:path=>calls.push(path),newsItems});try{
        const select=app.document.getElementById('newsSymbolSelect');
        select.add(new app.window.Option('MU','MU'));select.value='MU';
        app.document.getElementById('newsFocus').value='stock';select.dispatchEvent(new app.window.Event('change',{bubbles:true}));
        assert.match(app.document.getElementById('newsList').textContent,/MU news/);
        assert.doesNotMatch(app.document.getElementById('newsList').textContent,/NVDA news/);
        assert.equal(calls.length,0);assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});

test('Independent strategy page renders signals and runs a completed demo backtest',async()=>{
    const fixture=projectJSON(`import asyncio,json
from app.config import settings
from app.market import MarketData
from app.strategies import indicators,strategy_snapshot,backtest
data=asyncio.run(MarketData(settings).candles('NVDA','1d','demo'))
result=backtest(data['bars'],'ema_cross')
result.update(symbol=data['symbol'],interval='1d',strategy='ema_cross',source=data['source'],is_demo=True,data_notice=data['notice'],adjustment=data['adjustment'])
print(json.dumps({'market':{**data,'indicators':indicators(data['bars']),'strategies':strategy_snapshot(data['bars'])},'backtest':result}))`);
    const app=await workspace({url:'http://radar.test/strategies',defaultProvider:'demo',marketResponse:fixture.market,backtestResponse:fixture.backtest});try{
        assert.equal(visiblePage(app.document),'strategies');
        assert.equal(app.document.querySelectorAll('#strategyCards .strategy-card').length,5);
        assert.match(app.document.getElementById('researchDataStatus').textContent,/260 根 K 线/);
        app.document.getElementById('backtestForm').dispatchEvent(new app.window.Event('submit',{bubbles:true,cancelable:true}));
        await tick();
        assert.equal(app.document.getElementById('backtestResults').hidden,false);
        assert.equal(app.document.querySelectorAll('#backtestMetrics>div').length,4);
        assert.match(app.document.getElementById('backtestDetails').textContent,/合成数据/);
        assert.equal(app.document.getElementById('backtestError').hidden,true);
        assert.deepEqual(app.errors,[]);
    }finally{app.dom.window.close();}
});
