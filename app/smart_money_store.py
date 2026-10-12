"""Independent SEC archive. A complete filing and progress share one transaction."""
import json
import math
import re
import sqlite3
from datetime import datetime
from .smart_money_models import decimal_text, empty_progress, iso_date, normalize_cik, validate_ref


def init_smart_money_schema(conn: sqlite3.Connection) -> None:
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS sec_filings (
          accession TEXT PRIMARY KEY, cik TEXT NOT NULL, form TEXT NOT NULL,
          filed_at TEXT NOT NULL, report_period TEXT, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sec_cik_period ON sec_filings(cik,report_period,filed_at);
        CREATE TABLE IF NOT EXISTS institution_positions (
          accession TEXT NOT NULL REFERENCES sec_filings(accession), row_key TEXT NOT NULL,
          data TEXT NOT NULL, PRIMARY KEY(accession,row_key));
        CREATE TABLE IF NOT EXISTS insider_transactions (
          accession TEXT NOT NULL REFERENCES sec_filings(accession), row_key TEXT NOT NULL,
          issuer_cik TEXT NOT NULL, code TEXT NOT NULL, derivative INTEGER NOT NULL,
          transaction_date TEXT, data TEXT NOT NULL, PRIMARY KEY(accession,row_key));
        CREATE INDEX IF NOT EXISTS idx_insider_filter ON insider_transactions(issuer_cik,code,derivative);
        CREATE TABLE IF NOT EXISTS sec_progress (source_key TEXT PRIMARY KEY, data TEXT NOT NULL);
    ''')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False)


class SmartMoneyStore:
    def __init__(self,db):
        self.db=db

    def has_filing(self,accession:str)->bool:
        with self.db.lock:
            return self.db.conn.execute('SELECT 1 FROM sec_filings WHERE accession=?',(accession,)).fetchone() is not None

    def progress(self,source_key:str)->dict:
        with self.db.lock:
            row=self.db.conn.execute('SELECT data FROM sec_progress WHERE source_key=?',(source_key,)).fetchone()
            return empty_progress(source_key) | (json.loads(row[0]) if row else {})

    def _save_progress(self,progress):
        if not re.fullmatch(r'(institution|issuer):[0-9]{1,10}|sec:cooldown',progress['source_key']):
            raise ValueError('无效采集来源')
        for key in ('retry_at','last_success_at'):
            if progress.get(key) is not None and (not math.isfinite(progress[key]) or progress[key]<0):
                raise ValueError('无效采集时间')
        self.db.conn.execute('INSERT INTO sec_progress VALUES (?,?) ON CONFLICT(source_key) DO UPDATE SET data=excluded.data',
                             (progress['source_key'],encoded(progress)))

    def save_progress(self,progress:dict)->None:
        with self.db.lock,self.db.conn:
            self._save_progress(progress)

    def commit_filing(self,filing:dict,positions:list[dict],transactions:list[dict],progress:dict)->bool:
        validate_ref(filing)
        if datetime.fromisoformat(filing['received_at']).utcoffset() is None:
            raise ValueError('接收时间必须带时区')
        if filing['completeness'] not in ('complete','incomplete','unsupported') or filing['amendment_type'] not in ('none','new_holdings','restatement','unknown'):
            raise ValueError('无效申报状态')
        if positions and not filing['form'].startswith('13F-HR') or transactions and filing['form'] not in ('4','4/A'):
            raise ValueError('申报与数据类型不一致')
        for rows in (positions,transactions):
            keys=[r['row_key'] for r in rows]
            if len(set(keys))!=len(keys) or any(not k for k in keys):
                raise ValueError('申报行标识重复或为空')
        for position in positions:
            for key in ('shares','value_usd'):decimal_text(position[key],non_negative=True)
            if position['share_type'] not in ('SH','PRN') or position['put_call'] not in ('','PUT','CALL'):
                raise ValueError('无效证券类型')
        for row in transactions:
            if normalize_cik(row['issuer_cik'])!=filing['cik']:
                raise ValueError('发行人身份不一致')
            for key in ('shares','price','amount_usd','post_shares'):decimal_text(row[key],nullable=True)
            iso_date(row['transaction_date'])
        with self.db.lock,self.db.conn:
            if self.has_filing(filing['accession']):
                self._save_progress(progress)
                return False
            self.db.conn.execute('INSERT INTO sec_filings VALUES (?,?,?,?,?,?)',
                (filing['accession'],filing['cik'],filing['form'],filing['filed_at'],filing['report_period'],encoded(filing)))
            self.db.conn.executemany('INSERT INTO institution_positions VALUES (?,?,?)',
                [(filing['accession'],r['row_key'],encoded(r)) for r in positions])
            self.db.conn.executemany('INSERT INTO insider_transactions VALUES (?,?,?,?,?,?,?)',
                [(filing['accession'],r['row_key'],r['issuer_cik'],r['code'],int(r['derivative']),r['transaction_date'],encoded(r)) for r in transactions])
            self._save_progress(progress)
            return True

    def filings(self,*,cik:str|None=None,kind:str|None=None)->list[dict]:
        clauses,args=[],[]
        if cik:clauses.append('cik=?');args.append(cik)
        if kind:
            if kind not in ('institution','insider'):raise ValueError('无效申报类别')
            clauses.append("form IN ('13F-HR','13F-HR/A')" if kind=='institution' else "form IN ('4','4/A')")
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.db.lock:
            return [json.loads(r[0]) for r in self.db.conn.execute('SELECT data FROM sec_filings'+where+' ORDER BY filed_at DESC,accession DESC',args)]

    def positions(self,accession:str)->list[dict]:
        with self.db.lock:
            return [json.loads(r[0]) for r in self.db.conn.execute('SELECT data FROM institution_positions WHERE accession=? ORDER BY row_key',(accession,))]

    def transactions(self,*,issuer_cik:str|None=None,code:str|None=None,derivative:bool|None=None,limit:int=100,offset:int=0)->tuple[list[dict],int]:
        clauses,args=[],[]
        for column,value in [('issuer_cik',issuer_cik),('code',code),('derivative',derivative)]:
            if value is not None:clauses.append('t.'+column+'=?');args.append(value)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        query=' FROM insider_transactions t JOIN sec_filings f USING(accession)'+where
        with self.db.lock:
            total=self.db.conn.execute('SELECT COUNT(*)'+query,args).fetchone()[0]
            rows=self.db.conn.execute('SELECT t.data,f.data'+query+' ORDER BY f.filed_at DESC,f.accession DESC,t.row_key LIMIT ? OFFSET ?',(*args,limit,offset))
            items=[]
            for row in rows:
                filing=json.loads(row[1]);items.append(json.loads(row[0])|{k:filing[k] for k in ('accession','form','filed_at','source_url','report_period')})
            return items,total

    def all_progress(self)->list[dict]:
        with self.db.lock:
            return [empty_progress(r[0])|json.loads(r[1]) for r in self.db.conn.execute('SELECT source_key,data FROM sec_progress ORDER BY source_key')]
