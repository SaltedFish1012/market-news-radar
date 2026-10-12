"""Local disclosure views. These functions never create an upstream client."""
import time
from dataclasses import asdict
from .instruments import resolve_instrument
from .sec_13f import build_snapshots,compare_holdings,previous_quarter
from .sec_form4 import transaction_kind
from .smart_money import sec_contact_ready,resolve_issuer_scope
from .smart_money_models import empty_progress


def issuer_associations(store,settings)->dict[str,list[str]]:
    mapping={}
    for progress in store.all_progress():
        if progress['source_key'].startswith('issuer:'):
            mapping[progress['source_key'].split(':',1)[1]]=[s for s in progress['symbols'] if s not in settings.smart_money_issuer_map]
    for symbol,cik in settings.smart_money_issuer_map.items():
        if symbol not in mapping.setdefault(cik,[]):mapping[cik].append(symbol)
    return mapping


def smart_money_overview(store,settings,watchlist:dict)->dict:
    filings=store.filings();progress={p['source_key']:p for p in store.all_progress()}
    names={f['cik']:f['entity_name'] for f in filings}
    names.update({i['cik']:i['name'] for i in settings.smart_money_institutions})
    active={('institution:'+i['cik']):i.get('enabled',True) for i in settings.smart_money_institutions}
    mapping=issuer_associations(store,settings)
    confirmed={s:cik for cik,symbols in mapping.items() for s in symbols}
    records=[asdict(resolve_instrument(s,watchlist.get('instruments'))) for s in watchlist['symbols']]
    scope,unsupported=resolve_issuer_scope(records,confirmed,settings.smart_money_issuer_map)
    active.update({'issuer:'+cik:True for cik in scope})
    keys=set(progress)|set(active)|{('institution:' if f['form'].startswith('13F') else 'issuer:')+f['cik'] for f in filings}
    sources=[]
    for key in sorted(keys):
        p=progress.get(key,empty_progress(key));cik=key.split(':',1)[1]
        state='cooldown' if key=='sec:cooldown' and p['retry_at']>time.time() else 'error' if p['last_error'] else 'pending' if not p['initialized'] else p['coverage_state']
        if key=='sec:cooldown' and not p['last_error'] and not p['retry_at']:continue
        sources.append({k:p[k] for k in ('source_key','initialized','symbols','last_success_at','last_error','retry_at','failures','coverage_start','coverage_end','coverage_state')}|
            dict(name=names.get(cik,cik),enabled=bool(settings.smart_money_enabled and active.get(key,False)),state=state,pending_count=len(p['pending'])))
    _,insider_count=store.transactions(limit=1)
    return dict(configured={'enabled':settings.smart_money_enabled,'contact_configured':sec_contact_ready(settings.sec_contact)},
        sources=sources,scope={'supported':[{'cik':cik,'symbols':symbols,'association':'发行人关联；不同股类不合并为同一证券'} for cik,symbols in scope.items()],'unsupported':unsupported},
        recent_filings=filings[:10],counts={'filings':len(filings),'holdings':sum(len(store.positions(f['accession'])) for f in filings),'insiders':insider_count})


def holdings_view(store,*,cik=None,period=None,symbol=None,limit=100,offset=0,cusip_map:dict)->dict:
    filings=store.filings(cik=cik,kind='institution')
    snapshots=build_snapshots(filings,{f['accession']:store.positions(f['accession']) for f in filings})
    index={(s['cik'],s['report_period']):s for s in snapshots}
    reports=[];items=[]
    for snapshot in snapshots:
        if period and snapshot['report_period']!=period:continue
        result=compare_holdings(snapshot,index.get((snapshot['cik'],previous_quarter(snapshot['report_period']))),cusip_map)
        name=snapshot['filings'][-1]['entity_name']
        rows=[r|dict(cik=snapshot['cik'],entity_name=name,report_period=snapshot['report_period']) for r in result['items'] if not symbol or r['symbol']==symbol]
        if symbol and not rows:continue
        reports.append({k:v for k,v in result.items() if k!='items'}|dict(cik=snapshot['cik'],entity_name=name))
        items.extend(rows)
    return dict(reports=reports,items=items[offset:offset+limit],total=len(items),limit=limit,offset=offset)


def insiders_view(store,*,symbol=None,code=None,derivative=None,limit=100,offset=0,issuer_map:dict)->dict:
    if symbol:
        ciks=[cik for cik,symbols in issuer_map.items() if symbol in symbols]
        rows=[]
        for cik in ciks:
            part,count=store.transactions(issuer_cik=cik,code=code,derivative=derivative,limit=1)
            if count:part,_=store.transactions(issuer_cik=cik,code=code,derivative=derivative,limit=count)
            rows.extend(part)
        rows.sort(key=lambda r:(r['filed_at'],r['accession'],r['row_key']),reverse=True)
        total=len(rows);rows=rows[offset:offset+limit]
    else:rows,total=store.transactions(code=code,derivative=derivative,limit=limit,offset=offset)
    items=[r|dict(symbols=issuer_map.get(r['issuer_cik'],[]),kind=transaction_kind(r['code']),amended=r['form']=='4/A',
        active_buy_sell=r['code'] in ('P','S') and not r['derivative'] and r['form']=='4') for r in rows]
    return dict(items=items,total=total,limit=limit,offset=offset)
