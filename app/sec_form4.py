"""Ownership disclosure semantics; a report is not a real-time trade feed."""
from decimal import Decimal,localcontext
import re
from .sec_xml import parse_xml,local_name,elements,first,text
from .smart_money_models import decimal_text,iso_date,normalize_cik,validate_ref


def transaction_kind(code:str)->str:
    return {'P':'buy','S':'sell','A':'grant','M':'exercise','F':'tax','G':'gift'}.get(code,'other')


def flag(value:str)->bool|None:
    if not value:return None
    if value.lower() not in ('1','0','true','false'):raise ValueError('无效所有权标记')
    return value.lower() in ('1','true')


def field_value(root,name:str)->str:
    node=first(root,name)
    return text(node,'value') if node is not None else ''


def number_value(root,name:str)->str|None:
    raw=field_value(root,name)
    if not raw:return None
    try:
        return decimal_text(raw,non_negative=True)
    except ValueError:
        return None


def parse_form4(document:bytes,ref:dict,*,received_at:str)->tuple[dict,list[dict]]:
    validate_ref(ref);root=parse_xml(document)
    issuer=first(root,'issuer')
    if local_name(root)!='ownershipdocument' or issuer is None or ref['form'] not in ('4','4/A') or text(root,'documentType')!=ref['form']:
        raise ValueError('Form 4 文档类型无效')
    cik=normalize_cik(text(issuer,'issuerCik'))
    if cik!=ref['cik']:raise ValueError('Form 4 发行人身份不一致')
    issuer_name=text(issuer,'issuerName')
    period=iso_date(text(root,'periodOfReport') or None)
    if ref['report_period'] and period!=ref['report_period']:raise ValueError('Form 4 报告期不一致')
    filing=ref|dict(report_period=period,entity_name=issuer_name,received_at=received_at,
        amendment_type='unknown' if ref['form']=='4/A' else 'none',completeness='complete',
        detail='修订件：保留原件，不自动替换交易行。' if ref['form']=='4/A' else '')
    owners=[]
    for owner in elements(root,'reportingOwner'):
        name=text(owner,'rptOwnerName')
        if not name:raise ValueError('Form 4 报告人身份缺失')
        owners.append(dict(cik=normalize_cik(text(owner,'rptOwnerCik')),name=name,
            is_director=flag(text(owner,'isDirector')) or False,is_officer=flag(text(owner,'isOfficer')) or False,
            is_ten_percent_owner=flag(text(owner,'isTenPercentOwner')) or False,is_other=flag(text(owner,'isOther')) or False,
            officer_title=text(owner,'officerTitle'),other_text=text(owner,'otherText')))
    if not owners or not issuer_name:raise ValueError('Form 4 身份缺失')
    footnotes={node.attrib['id']:''.join(node.itertext()).strip() for node in elements(root,'footnote') if node.attrib.get('id')}
    ten_b5=flag(text(root,'aff10b5One'))
    rows=[]
    for kind,tag in [('non_derivative','nonDerivativeTransaction'),('derivative','derivativeTransaction')]:
        for index,node in enumerate(elements(root,tag)):
            code=text(node,'transactionCode').upper()
            if not re.fullmatch(r'[A-Z]',code):raise ValueError('Form 4 交易代码无效')
            shares=number_value(node,'transactionShares');price=number_value(node,'transactionPricePerShare')
            price_node=first(node,'transactionPricePerShare')
            share_node=first(node,'transactionShares')
            ambiguous=any(elements(n,'footnoteId') for n in (price_node,share_node) if n is not None)
            amount=None
            if shares is not None and price is not None and not ambiguous:
                with localcontext() as context:
                    context.prec=220;amount=format(Decimal(shares)*Decimal(price),'f')
            rows.append(dict(row_key=f'{kind}:{index}',issuer_cik=cik,issuer_name=issuer_name,
                security_title=field_value(node,'securityTitle'),owners=owners,derivative=kind=='derivative',
                transaction_date=iso_date(field_value(node,'transactionDate') or None),code=code,
                acquired_disposed=field_value(node,'transactionAcquiredDisposedCode'),shares=shares,price=price,
                amount_usd=amount,post_shares=number_value(node,'sharesOwnedFollowingTransaction'),
                ownership=field_value(node,'directOrIndirectOwnership'),ownership_nature=field_value(node,'natureOfOwnership'),
                footnotes=footnotes,ten_b5_1=ten_b5))
    return filing,rows
