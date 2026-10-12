"""Read-only official SEC discovery with bounded serial requests."""
import asyncio
import json
import math
import re
import time
from email.utils import parsedate_to_datetime
import httpx
from .smart_money_models import normalize_cik, iso_date, validate_ref
from .sec_xml import MAX_RESPONSE_BYTES, parse_xml, local_name


class SECError(Exception):
    def __init__(self,kind:str,status_code:int|None=None,retry_at:float|None=None):
        self.kind,self.status_code,self.retry_at=kind,status_code,retry_at
        super().__init__('SEC: '+kind+(f' (HTTP {status_code})' if status_code else ''))


def contact_ready(contact:str)->bool:
    return isinstance(contact,str) and len(contact)<=250 and not any(ord(c)<32 for c in contact) and bool(re.search(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+',contact))


def document_name(value:str)->str:
    if not isinstance(value,str): raise ValueError('无效 SEC 文档路径')
    value=re.sub(r'^(?:xslF345X[0-9]{2}|xslForm13F_X[0-9]{2})/','',value)
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}',value) or '..' in value:
        raise ValueError('无效 SEC 文档路径')
    return value


def filing_refs(payload:dict,cik:str)->list[dict]:
    cik=normalize_cik(cik)
    try:
        rows=payload['filings']['recent'] if 'filings' in payload else payload
        required=['accessionNumber','form','filingDate','primaryDocument']
        size=len(rows['accessionNumber'])
        if any(not isinstance(rows.get(k),list) or len(rows[k])!=size for k in required): raise ValueError()
        if 'reportDate' in rows and (not isinstance(rows['reportDate'],list) or len(rows['reportDate'])!=size): raise ValueError()
        result=[]
        for i in range(size):
            accession=rows['accessionNumber'][i];form=rows['form'][i]
            if not isinstance(accession,str) or not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}',accession): raise ValueError()
            if not isinstance(form,str) or not form or len(form)>30: raise ValueError()
            primary=rows['primaryDocument'][i];raw=document_name(primary)
            filed=iso_date(rows['filingDate'][i]);period=iso_date((rows.get('reportDate') or [None]*size)[i] or None)
            if filed is None: raise ValueError()
            result.append(dict(accession=accession,cik=cik,form=form,filed_at=filed,report_period=period,
                primary_document=primary,source_url=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-","")}/{raw}'))
        return result
    except (KeyError,TypeError,ValueError) as exc:
        raise SECError('invalid_response') from exc


class SECClient:
    def __init__(self,contact:str,transport=None,*,clock=time.time,sleep=asyncio.sleep):
        if not contact_ready(contact): raise SECError('setup_required')
        self.clock,self.sleep=clock,sleep
        self.lock=asyncio.Lock();self.last_start=None
        self.http=httpx.AsyncClient(timeout=20,follow_redirects=False,transport=transport,
            headers={'User-Agent':'MarketNewsRadar/0.4 '+contact,'Accept-Encoding':'gzip, deflate'})

    async def close(self):
        await self.http.aclose()

    async def _request(self,url:str)->bytes:
        async with self.lock:
            if self.last_start is not None:
                delay=.5-(self.clock()-self.last_start)
                if delay>0: await self.sleep(delay)
            self.last_start=self.clock()
            try:
                async with self.http.stream('GET',url) as response:
                    if response.status_code!=200:
                        retry=None;raw=response.headers.get('Retry-After')
                        if raw:
                            try: retry=self.clock()+max(0,float(raw))
                            except ValueError:
                                try: retry=parsedate_to_datetime(raw).timestamp()
                                except (ValueError,TypeError,OverflowError): pass
                            if retry is not None and not math.isfinite(retry): retry=None
                        kind={403:'permission',429:'rate_limit',404:'not_found'}.get(response.status_code,'upstream')
                        raise SECError(kind,response.status_code,retry if response.status_code in (403,429) else None)
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data)+len(chunk)>MAX_RESPONSE_BYTES: raise SECError('response_too_large',200)
                        data.extend(chunk)
                    return bytes(data)
            except httpx.HTTPError as exc:
                raise SECError('network') from exc

    async def _json(self,url:str)->dict:
        raw=await self._request(url)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict): raise ValueError()
            return data
        except (ValueError,UnicodeError) as exc: raise SECError('invalid_response',200) from exc

    async def submissions(self,cik:str)->dict:
        return await self._json(f'https://data.sec.gov/submissions/CIK{normalize_cik(cik).zfill(10)}.json')

    async def submission_file(self,cik:str,name:str)->dict:
        cik=normalize_cik(cik)
        if not isinstance(name,str) or not re.fullmatch(r'CIK'+cik.zfill(10)+r'-submissions-[0-9]{3,6}\.json',name):
            raise ValueError('无效 SEC 历史分页路径')
        return await self._json('https://data.sec.gov/submissions/'+name)

    async def ticker_index(self)->dict[str,str]:
        data=await self._json('https://www.sec.gov/files/company_tickers.json')
        result={}
        try:
            for row in data.values():
                ticker=row['ticker'];cik=row['cik_str']
                if not isinstance(ticker,str) or not re.fullmatch(r'[A-Z0-9.-]{1,20}',ticker) or isinstance(cik,bool) or not isinstance(cik,int): raise ValueError()
                canonical=normalize_cik(str(cik))
                if ticker in result and result[ticker]!=canonical: raise ValueError()
                result[ticker]=canonical
            return result
        except (ValueError,KeyError,TypeError) as exc: raise SECError('invalid_response',200) from exc

    async def filing_documents(self,ref:dict)->tuple[bytes,list[bytes]]:
        validate_ref(ref)
        primary=document_name(ref['primary_document'])
        base=f'https://www.sec.gov/Archives/edgar/data/{normalize_cik(ref["cik"])}/{ref["accession"].replace("-","")}/'
        cover=await self._request(base+primary)
        try:
            root=local_name(parse_xml(cover))
            expected='ownershipdocument' if ref['form'] in ('4','4/A') else 'edgarsubmission'
            if root!=expected: raise ValueError()
            if expected=='ownershipdocument': return cover,[]
            manifest=await self._json(base+'index.json')
            entries=manifest['directory']['item']
            if not isinstance(entries,list): raise ValueError()
            names=list(dict.fromkeys(document_name(row['name']) for row in entries if str(row.get('name','')).lower().endswith('.xml')))
            if len(names)>50: raise SECError('too_many_documents')
            tables=[]
            for name in names:
                if name==primary: continue
                document=await self._request(base+name)
                if local_name(parse_xml(document))=='informationtable': tables.append(document)
            if not tables and ref['filed_at']>='2023-01-03': raise ValueError()
            return cover,tables
        except (ValueError,KeyError,TypeError) as exc: raise SECError('invalid_response',200) from exc
