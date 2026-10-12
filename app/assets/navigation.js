'use strict';
const workspacePages={
 market:{path:'/',view:'marketView',section:'marketSection',label:'市场总览'},
 strategies:{path:'/strategies',view:'strategyView',section:'strategySection',label:'策略与回测'},
 news:{path:'/news',view:'newsView',section:'newsPanel',label:'新闻中心'},
 alerts:{path:'/alerts',view:'alertsView',section:'alertsSection',label:'预警记录'},
 sources:{path:'/sources',view:'sourcesView',section:'sourcesSection',label:'数据连接'},
 settings:{path:'/settings',view:'settingsView',section:'settingsSection',label:'设置'}
};
let activePage='market';
function pageFromLocation(){const hash=location.hash.slice(1);if(hash){const legacy=Object.entries(workspacePages).find(([key,page])=>hash===key||hash===page.section);if(legacy)return legacy[0];}return Object.entries(workspacePages).find(([,page])=>page.path===location.pathname)?.[0]||'market';}
function showPage(key){if(typeof invalidateXRequests==='function')invalidateXRequests();const page=workspacePages[key]||workspacePages.market;activePage=workspacePages[key]?key:'market';document.querySelectorAll('[data-page]').forEach(view=>view.hidden=view.dataset.page!==activePage);$('workspaceView').hidden=activePage==='settings';if($('researchWorkspace'))$('researchWorkspace').hidden=activePage==='settings';document.querySelectorAll('[data-section]').forEach(link=>{const selected=link.dataset.section===page.section;link.classList.toggle('active',selected);if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});$('breadcrumb').textContent='工作台 / '+page.label;document.title=page.label+' · Market Radar';window.scrollTo({top:0,left:0,behavior:'instant'});if(activePage==='market')drawPrice();if(activePage==='strategies')drawEquity();}
function navigatePage(key){const page=workspacePages[key];if(!page)return;if(location.pathname!==page.path||location.hash)history.pushState({page:key},'',page.path);showPage(key);ensurePageData();if(key==='settings'&&state.loggedIn&&!settingsState.snapshot&&!settingsState.busy)loadSettings();}
function bindPageNavigation(){document.querySelectorAll('[data-section]').forEach(link=>link.addEventListener('click',event=>{if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button>0)return;event.preventDefault();const match=Object.entries(workspacePages).find(([,page])=>page.section===link.dataset.section);if(match)navigatePage(match[0]);}));window.addEventListener('popstate',()=>{showPage(pageFromLocation());ensurePageData();if(activePage==='settings'&&state.loggedIn&&!settingsState.snapshot&&!settingsState.busy)loadSettings();});}
