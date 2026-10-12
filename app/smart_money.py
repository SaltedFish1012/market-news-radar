"""Durable, read-only SEC collection; independent of news and notifications."""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from .instruments import resolve_instrument
from .sec_client import SECClient, SECError, contact_ready, filing_refs
from .sec_13f import parse_13f, quarter_date, previous_quarter, build_snapshots
from .sec_form4 import parse_form4
from .smart_money_models import normalize_cik, empty_progress
from .smart_money_store import SmartMoneyStore
from .sec_xml import parse_xml, text

sec_contact_ready=contact_ready


def resolve_issuer_scope(instruments:list[dict],ticker_index:dict[str,str],overrides:dict[str,str])->tuple[dict,list]:
    scope={};unsupported=[]
    for record in instruments:
        instrument=resolve_instrument(record['symbol'],[record])
        if instrument.market!='US' or instrument.asset_type!='stock':
            unsupported.append({'symbol':instrument.symbol,'reason':'仅美股股票支持发行人内部人披露；ETF、指数及其他市场未纳入。'})
            continue
        cik=overrides.get(instrument.symbol) or ticker_index.get(instrument.symbol)
        if not cik:
            unsupported.append({'symbol':instrument.symbol,'reason':'SEC 官方代码表无精确匹配，请配置发行人 CIK。'})
            continue
        symbols=scope.setdefault(normalize_cik(cik),[])
        if instrument.symbol not in symbols:symbols.append(instrument.symbol)
    return scope,unsupported


async def checkpoint():
    # Even a transport that returns synchronously after cancel() cannot commit.
    await asyncio.sleep(0)
    task=asyncio.current_task()
    if task and task.cancelling(): raise asyncio.CancelledError()


def retry_at(error,failures,now):
    return max(now,error.retry_at) if isinstance(error,SECError) and error.retry_at is not None else now+min(3600,30*2**min(20,max(0,failures-1)))


def cooldown(store,error,now):
    state=store.progress('sec:cooldown')
    state.update(failures=state['failures']+1,last_error=error.kind)
    state['retry_at']=retry_at(error,state['failures'],now)
    store.save_progress(state)


async def discover(client,cik,kind,state,store,now):
    payload=await client.submissions(cik);await checkpoint()
    refs=filing_refs(payload,cik)
    forms=('13F-HR','13F-HR/A') if kind=='institution' else ('4','4/A')
    selected=[r for r in refs if r['form'] in forms]
    cutoff=(datetime.fromtimestamp(now,timezone.utc)-timedelta(days=90)).date().isoformat()
    def bounds():
        periods=[r['report_period'] for r in selected if r['report_period']]
        if kind=='institution':
            periods += [f['report_period'] for f in store.filings(cik=cik,kind='institution') if f['report_period']]
            if state['coverage_end']:periods.append(state['coverage_end'])
            latest=max(periods,default=None)
            return (previous_quarter(quarter_date(latest)),latest) if latest else (None,None)
        return cutoff,datetime.fromtimestamp(now,timezone.utc).date().isoformat()
    files=payload.get('filings',{}).get('files',[])
    if not isinstance(files,list):raise SECError('invalid_response')
    for page in sorted(files,key=lambda p:p.get('filingTo',''),reverse=True):
        start,_=bounds()
        # A filing cannot report a quarter that has not ended yet. Read all
        # supplemental files overlapping the lower boundary, including amendments.
        if start and page.get('filingTo','9999-12-31')<start:continue
        older=await client.submission_file(cik,page['name']);await checkpoint()
        selected.extend(r for r in filing_refs(older,cik) if r['form'] in forms)
    cache={};failed=set();parse_error=None;remaining=20
    prior_pending={r['accession'] for r in state['pending']}
    if kind=='institution':
        for ref in selected:
            if ref['report_period'] is not None or ref['accession'] in cache or ref['accession'] in failed:continue
            if remaining==0:break
            if not any(r['accession']==ref['accession'] for r in state['pending']):state['pending'].append(ref.copy())
            await checkpoint();store.save_progress(state)
            remaining-=1
            try:
                document,tables=await client.filing_documents(ref);await checkpoint()
                root=parse_xml(document)
                period=quarter_date(text(root,'periodOfReport') or text(root,'reportCalendarOrQuarter'))
                ref['report_period']=period;cache[ref['accession']]=(document,tables)
                for queued in state['pending']:
                    if queued['accession']==ref['accession']:queued['report_period']=period
            except (SECError,ValueError,KeyError) as exc:
                if isinstance(exc,SECError) and exc.status_code in (403,429):raise
                failed.add(ref['accession']);parse_error=exc
    start,end=bounds()
    if kind=='institution':
        selected=[r for r in selected if not r['report_period'] or r['report_period'] in (start,end)]
        allowed={r['accession'] for r in selected}
        state['pending']=[r for r in state['pending'] if r['accession'] in prior_pending or r['accession'] in allowed]
    else:
        selected=[r for r in selected if r['filed_at']>=cutoff or (state['initialized'] and state['last_success_at'] is not None and r['filed_at']>=datetime.fromtimestamp(state['last_success_at'],timezone.utc).date().isoformat())]
    return selected,start,end,cache,failed,parse_error,remaining


async def collect_source(engine,config,client,store,kind,cik,symbols,now):
    state=store.progress(kind+':'+cik)
    if state['retry_at']>now:return
    state['symbols']=symbols
    discovery_error=None
    cache={};failed=set();remaining=20
    try:
        refs,start,end,cache,failed,discovery_error,remaining=await discover(client,cik,kind,state,store,now)
        known={r['accession'] for r in state['pending']}
        for ref in sorted(refs,key=lambda r:(r['filed_at'],r['accession'])):
            if ref['accession'] not in known and not store.has_filing(ref['accession']):
                state['pending'].append(ref);known.add(ref['accession'])
        state.update(initialized=True,coverage_start=start,coverage_end=end,coverage_state='partial')
        await checkpoint();store.save_progress(state)
    except (SECError,ValueError,KeyError) as error:
        if isinstance(error,SECError) and error.status_code in (403,429):
            await checkpoint();cooldown(store,error,now);raise
        discovery_error=error
    error=discovery_error
    for ref in list(state['pending'])[:20]:
        if ref['accession'] in failed:continue
        if ref['accession'] not in cache and remaining==0:break
        try:
            if ref['accession'] in cache:document,tables=cache[ref['accession']]
            else:
                remaining-=1
                document,tables=await client.filing_documents(ref);await checkpoint()
            stamp=datetime.fromtimestamp(now,timezone.utc).isoformat()
            if kind=='institution':
                filing,positions=parse_13f(document,tables,ref,received_at=stamp);transactions=[]
                if state['coverage_end'] is None or filing['report_period']>state['coverage_end']:
                    state.update(coverage_end=filing['report_period'],coverage_start=previous_quarter(filing['report_period']))
            else:
                filing,transactions=parse_form4(document,ref,received_at=stamp);positions=[]
            next_state=state|{'pending':[r for r in state['pending'] if r['accession']!=ref['accession']],
                'last_success_at':now,'last_error':'','retry_at':0,'failures':0}
            await checkpoint();store.commit_filing(filing,positions,transactions,next_state);state=next_state
        except (SECError,ValueError,KeyError) as exc:
            if isinstance(exc,SECError) and exc.status_code in (403,429):
                state.update(last_error=exc.kind,coverage_state='partial')
                await checkpoint();store.save_progress(state);cooldown(store,exc,now);raise
            error=exc
    if kind=='institution':
        filings=store.filings(cik=cik,kind=kind)
        snapshots=build_snapshots(filings,{f['accession']:store.positions(f['accession']) for f in filings})
        complete={s['report_period'] for s in snapshots if s['complete']}
        covered=bool(state['coverage_end'] and state['coverage_start'] in complete and state['coverage_end'] in complete)
    else:covered=state['initialized']
    state['coverage_state']='complete' if covered and not state['pending'] and not error else 'partial'
    if error:
        state.update(last_error=error.kind if isinstance(error,SECError) else 'invalid_filing',failures=state['failures']+1)
        state['retry_at']=retry_at(error,state['failures'],now)
    elif not state['pending']:
        state.update(last_success_at=now,last_error='',retry_at=0,failures=0)
    await checkpoint();store.save_progress(state)


async def collect_round(engine,settings,client:SECClient,*,now:float)->None:
    if not settings.smart_money_enabled or not sec_contact_ready(settings.sec_contact):return
    store=SmartMoneyStore(engine.db)
    if store.progress('sec:cooldown')['retry_at']>now:
        engine.db.health('smart_money','cooldown','SEC 要求等待，现有归档仍可查阅。');return
    try:
        records=[asdict(resolve_instrument(s,engine.watchlist.get('instruments'))) for s in engine.watchlist['symbols']]
        has_stocks=any(r['market']=='US' and r['asset_type']=='stock' for r in records)
        index=await client.ticker_index() if has_stocks else {};await checkpoint()
        scope,_=resolve_issuer_scope(records,index,settings.smart_money_issuer_map)
        sources=[('institution',normalize_cik(i['cik']),[]) for i in settings.smart_money_institutions if i.get('enabled',True)]
        sources += [('issuer',cik,symbols) for cik,symbols in scope.items()]
        for kind,cik,symbols in sources:
            await collect_source(engine,settings,client,store,kind,cik,symbols,now);await checkpoint()
        state=store.progress('sec:cooldown');state.update(retry_at=0,failures=0,last_error='')
        await checkpoint();store.save_progress(state)
        engine.db.health('smart_money','ok','SEC 采集轮次结束；覆盖状态见聪明钱页面。')
    except (SECError,ValueError,KeyError) as error:
        await checkpoint()
        if isinstance(error,SECError) and error.status_code in (403,429):
            # collect_source already persisted its shared cooldown.
            if store.progress('sec:cooldown')['retry_at']<=now:cooldown(store,error,now)
            engine.db.health('smart_money','cooldown','SEC 暂停访问，按要求等待后重试。')
        else:engine.db.health('smart_money','error','SEC 发现失败，现有归档仍可查阅。')


async def smart_money_loop(engine,settings)->None:
    client=None
    try:
        if settings.smart_money_enabled and sec_contact_ready(settings.sec_contact):client=SECClient(settings.sec_contact)
        else:engine.db.health('smart_money','disabled' if not settings.smart_money_enabled else 'setup_required','采集未启用或尚未填写 SEC 联系邮箱；现有归档仍可查阅。')
        while True:
            if client:await collect_round(engine,settings,client,now=time.time())
            await asyncio.sleep(settings.smart_money_poll_interval)
    finally:
        if client:await client.close()
