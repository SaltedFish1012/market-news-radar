const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=require('node:path').resolve(__dirname,'..');
const python=process.env.RADAR_TEST_PYTHON||root+(process.platform==='win32'?'/.venv/Scripts/python.exe':'/.venv/bin/python');
function projectJSON(script){return JSON.parse(execFileSync(python,['-c',script],{cwd:root,encoding:'utf8'}));}
const initial=projectJSON(`import json,tempfile
from pathlib import Path
from dataclasses import replace
from app.config import settings
from app.settings_store import SettingsStore
with tempfile.TemporaryDirectory() as folder:
    base=replace(settings,db_path=str(Path(folder)/'test.db'),ai_key='',ai_enabled=False,ai_provider='openai',
        ai_model='gpt-4o-mini',ai_base_url='https://api.openai.com/v1',alpaca_key='',alpaca_secret='',
        alpaca_news_enabled=False,lb_enabled=False,lb_news_enabled=False,lb_app_key='',lb_app_secret='',
        lb_access_token='',telegram_token='',telegram_chat='',bark_key='',
        watchlist_override={'symbols':['NVDA'],'price_rules':[]},feeds_override=[])
    print(json.dumps(SettingsStore(base).snapshot()))`);
const pageHtml=projectJSON("import json;from app.main import render_dashboard,WORKSPACE_PAGES;print(json.dumps({data[0]:render_dashboard(key).body.decode() for key,data in WORKSPACE_PAGES.items()}))");
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
async function workspace({url='http://radar.test/settings',failed=false,authenticated=true,onLogin=()=>{},onMarket=()=>{},newsItems=[],marketResponse=null,backtestResponse=null,defaultProvider='yahoo',quoteItems=[],onRequest=null}={}){
    const errors=[];const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
    const dom=new JSDOM(pageHtml[new URL(url).pathname]||pageHtml['/'],{url,runScripts:'dangerously',virtualConsole:console,beforeParse(window){
        window.HTMLElement.prototype.scrollIntoView=function(){throw Error('Navigation must not scroll to a section');};window.scrollTo=options=>window.lastScroll=options;
        window.ResizeObserver=class{observe(){}};
        window.fetch=async (path,options={})=>{
            if(onRequest){const custom=await onRequest(path,options);if(custom)return custom;}
            if(path==='/api/quotes')return{ok:true,status:200,json:async()=>({items:[],sessions:quoteItems})};
            if(path==='/api/session'&&options.method==='POST'){
                onLogin(JSON.parse(options.body));authenticated=true;
                return{ok:true,status:200,json:async()=>({authenticated:true})};
            }
            if(path==='/api/status')return{ok:authenticated,status:authenticated?200:401,json:async()=>({source_health:[]})};
            if(path==='/api/settings')return{ok:!failed,status:failed?503:200,json:async()=>failed?{detail:'配置读取暂不可用'}:structuredClone(initial)};
            if(path==='/api/watchlist')return{ok:true,status:200,json:async()=>({symbols:initial.watchlist.symbols,strategies:[],market_provider:defaultProvider})};
            if(path.startsWith('/api/strategies/backtest')&&backtestResponse)return{ok:true,status:200,json:async()=>structuredClone(backtestResponse)};
            if(path.startsWith('/api/market/')){onMarket(path);if(marketResponse)return{ok:true,status:200,json:async()=>structuredClone(path.includes('/candles')?marketResponse:{items:[]})};return new Promise(()=>{});} // Default: market never completes.
            if(path.startsWith('/api/news'))return{ok:true,status:200,json:async()=>({items:newsItems})};if(path.startsWith('/api/alerts'))return{ok:true,status:200,json:async()=>({items:[]})};
            throw Error('Unexpected request '+path);
        };
    }});
    for(const name of ['navigation.js','dashboard.js','settings.js']){const script=dom.window.document.createElement('script');script.textContent=fs.readFileSync(root+'/app/assets/'+name,'utf8');dom.window.document.body.appendChild(script);}
    await tick();await tick();
    return{dom,window:dom.window,document:dom.window.document,errors};
}

module.exports={workspace,tick,initial,projectJSON};
