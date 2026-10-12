"""Reported quarterly positions, not real-time flows or inferred trades."""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal,localcontext
import json
import re
from .sec_xml import parse_xml,local_name,elements,first,text
from .smart_money_models import decimal_text,normalize_cik,validate_ref


def quarter_date(value:str)->str:
    for fmt in ('%Y-%m-%d','%m-%d-%Y'):
        try:
            parsed=datetime.strptime(value,fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError('13F 报告期日期无效')
    if (parsed.month,parsed.day) not in ((3,31),(6,30),(9,30),(12,31)):
        raise ValueError('13F 报告期须为季度末')
    return parsed.isoformat()


def previous_quarter(period:str)->str:
    value=date.fromisoformat(quarter_date(period))
    return {(3,31):f'{value.year-1}-12-31',(6,30):f'{value.year}-03-31',
            (9,30):f'{value.year}-06-30',(12,31):f'{value.year}-09-30'}[(value.month,value.day)]


def add_numbers(left:str,right:str)->str:
    with localcontext() as context:
        context.prec=220
        return format(Decimal(left)+Decimal(right),'f')


def parse_13f(cover:bytes,tables:list[bytes],ref:dict,*,received_at:str)->tuple[dict,list[dict]]:
    validate_ref(ref)
    root=parse_xml(cover)
    if local_name(root)!='edgarsubmission' or text(root,'submissionType')!=ref['form'] or normalize_cik(text(root,'cik'))!=ref['cik']:
        raise ValueError('13F 封面身份不一致')
    period=quarter_date(text(root,'periodOfReport') or text(root,'reportCalendarOrQuarter'))
    if (ref['report_period'] and ref['report_period']!=period) or (text(root,'reportCalendarOrQuarter') and quarter_date(text(root,'reportCalendarOrQuarter'))!=period):
        raise ValueError('13F 报告期不一致')
    amendment='none'
    if ref['form']=='13F-HR/A':
        amendment={'NEW HOLDINGS':'new_holdings','RESTATEMENT':'restatement'}.get(text(root,'amendmentType').upper(),'unknown')
        if text(root,'isAmendment').lower() not in ('true','1'):amendment='unknown'
    elif text(root,'isAmendment').lower() in ('true','1'):
        amendment='unknown'
    manager=first(root,'filingManager')
    filing=ref|dict(report_period=period,entity_name=text(manager if manager is not None else root,'name'),
        received_at=received_at,amendment_type=amendment,completeness='complete',detail='')
    if ref['filed_at']<'2023-01-03':
        return filing|dict(completeness='unsupported',detail='2023-01-03 以前申报为旧单位，第一版不解析。'),[]
    if not tables:raise ValueError('13F 信息表缺失')
    positions={};count=0;total='0'
    for table in tables:
        info=parse_xml(table)
        if local_name(info)!='informationtable':raise ValueError('13F 信息表类型无效')
        for row in elements(info,'infoTable'):
            cusip=text(row,'cusip').upper();security=text(row,'titleOfClass')
            share_type=text(row,'sshPrnamtType').upper();put_call=text(row,'putCall').upper()
            if not re.fullmatch(r'[A-Z0-9*@#]{9}',cusip) or not security or not text(row,'nameOfIssuer') or share_type not in ('SH','PRN') or put_call not in ('','PUT','CALL'):
                raise ValueError('13F 证券字段无效')
            position=dict(issuer_name=text(row,'nameOfIssuer'),security_class=security,cusip=cusip,
                shares=decimal_text(text(row,'sshPrnamt'),non_negative=True),
                value_usd=decimal_text(text(row,'value'),non_negative=True),share_type=share_type,put_call=put_call)
            key=json.dumps([cusip,' '.join(security.upper().split()),put_call,share_type],separators=(',',':'))
            position['row_key']=key;count+=1;total=add_numbers(total,position['value_usd'])
            if key in positions:
                for field in ('shares','value_usd'):positions[key][field]=add_numbers(positions[key][field],position[field])
            else:positions[key]=position
    reasons=[]
    if text(root,'isConfidentialOmitted').lower() in ('true','1'):reasons.append('存在保密省略')
    if amendment=='unknown':reasons.append('修订类型无法解释')
    try:
        if int(text(root,'tableEntryTotal'))!=count or Decimal(decimal_text(text(root,'tableValueTotal')))!=Decimal(total):
            reasons.append('封面汇总与信息表不一致')
    except ValueError:
        reasons.append('封面汇总缺失或无效')
    if reasons:filing.update(completeness='incomplete',detail='；'.join(reasons))
    return filing,list(positions.values())


def build_snapshots(filings:list[dict],positions_by_accession:dict[str,list[dict]])->list[dict]:
    groups=defaultdict(list)
    for filing in filings:
        if filing['report_period']:groups[(filing['cik'],filing['report_period'])].append(filing)
    snapshots=[]
    for (cik,period),reports in sorted(groups.items(),reverse=True):
        reports.sort(key=lambda f:(f['filed_at'],f['accession']))
        positions={};complete=False;detail='缺少完整原始申报'
        for filing in reports:
            rows=positions_by_accession.get(filing['accession'],[])
            if filing['completeness']!='complete':
                complete=False;detail=filing['detail'] or '申报不完整'
                for row in rows:positions.setdefault(row['row_key'],row.copy())
            elif filing['form']=='13F-HR' or filing['amendment_type']=='restatement':
                positions={r['row_key']:r.copy() for r in rows};complete=True;detail=''
            elif filing['amendment_type']=='new_holdings':
                if not complete or set(positions)&{r['row_key'] for r in rows}:
                    complete=False;detail='补充修订缺少完整基底或包含重叠证券'
                for row in rows:positions.setdefault(row['row_key'],row.copy())
            else:
                complete=False;detail='修订类型无法解释'
        snapshots.append(dict(cik=cik,report_period=period,complete=complete,filings=reports,
                              positions=list(positions.values()),detail=detail))
    return snapshots


def compare_holdings(current:dict,previous:dict|None,cusip_map:dict[str,str])->dict:
    adjacent=previous is not None and current['cik']==previous['cik'] and previous['report_period']==previous_quarter(current['report_period'])
    state='not_comparable' if not current['complete'] else 'baseline_missing' if not adjacent else 'not_comparable' if not previous['complete'] else 'compared'
    current_rows={p['row_key']:p for p in current['positions']}
    old_rows={p['row_key']:p for p in previous['positions']} if state=='compared' else {}
    items=[]
    for key in sorted(current_rows.keys()|old_rows.keys()):
        row=current_rows.get(key) or old_rows[key]|{'shares':'0','value_usd':'0'}
        delta=None;change='unknown'
        if state=='compared':
            delta=add_numbers(row['shares'],'-'+old_rows[key]['shares'] if key in old_rows else '0')
            change='new' if key not in old_rows else 'exit' if key not in current_rows else 'increase' if Decimal(delta)>0 else 'decrease' if Decimal(delta)<0 else 'unchanged'
        items.append(row|dict(symbol=cusip_map.get(row['cusip']),change=change,shares_delta=delta))
    return dict(items=items,period=current['report_period'],previous_period=previous['report_period'] if previous else None,
                comparison_state=state,complete=current['complete'],filings=current['filings'],detail=current['detail'])
