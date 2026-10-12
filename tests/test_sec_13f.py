from pathlib import Path
import pytest
from sec_helpers import make_ref,make_filing,make_position

FIXTURES=Path(__file__).parent/'fixtures'/'sec'


def snapshot(period='2026-06-30',positions=None,complete=True):
    return dict(cik='1067983',report_period=period,complete=complete,filings=[],
                positions=positions if positions is not None else [make_position()],detail='')


def test_missing_adjacent_baseline_is_unknown():
    from app.sec_13f import compare_holdings
    for previous in [None,snapshot('2025-12-31')]:
        result=compare_holdings(snapshot(),previous,{})
        assert result['comparison_state']=='baseline_missing'
        assert all(p['change']=='unknown' and p['shares_delta'] is None for p in result['items'])
    result=compare_holdings(snapshot(complete=False),snapshot('2026-03-31'),{})
    assert result['comparison_state']=='not_comparable'


def test_quarter_changes_are_share_differences():
    from app.sec_13f import compare_holdings
    prior=[make_position(shares='80'),make_position(row_key='exit',cusip='037833100',shares='40'),
           make_position(row_key='reduce',shares='100')]
    current=[make_position(shares='100'),make_position(row_key='new',cusip='594918104'),
             make_position(row_key='reduce',shares='90')]
    result=compare_holdings(snapshot(positions=current),snapshot('2026-03-31',prior),{'67066G104':'NVDA'})
    assert result['comparison_state']=='compared'
    rows={r['row_key']:r for r in result['items']}
    assert rows[make_position()['row_key']]['shares_delta']=='20'
    assert rows['exit']['change']=='exit' and rows['exit']['shares_delta']=='-40'
    assert rows['new']['change']=='new' and rows['new']['symbol'] is None
    assert rows['reduce']['change']=='decrease' and rows['reduce']['shares_delta']=='-10'


def test_amendments_require_interpretable_complete_snapshot():
    from app.sec_13f import build_snapshots
    original=make_filing();acc2='0001067983-26-000002'
    amended=make_filing(accession=acc2,form='13F-HR/A',filed_at='2026-08-15',amendment_type='new_holdings')
    first=make_position();second=make_position(row_key='second',cusip='037833100')
    result=build_snapshots([original,amended],{original['accession']:[first],acc2:[second]})[0]
    assert result['complete'] and len(result['positions'])==2
    result=build_snapshots([original,amended|{'amendment_type':'restatement'}],{original['accession']:[first],acc2:[second]})[0]
    assert result['complete'] and result['positions']==[second]
    for bad in [amended|{'amendment_type':'unknown'},amended|{'completeness':'incomplete'}]:
        assert not build_snapshots([original,bad],{original['accession']:[first],acc2:[second]})[0]['complete']
    assert not build_snapshots([original,amended],{original['accession']:[first],acc2:[first]})[0]['complete']
    assert not build_snapshots([amended],{acc2:[second]})[0]['complete']


def test_position_keys_units_and_mapping():
    from app.sec_13f import parse_13f,compare_holdings
    cover=(FIXTURES/'13f_cover.xml').read_bytes();table=(FIXTURES/'13f_table.xml').read_bytes()
    filing,rows=parse_13f(cover,[table],make_ref(),received_at='2026-10-12T00:00:00Z')
    assert filing['report_period']=='2026-06-30' and filing['completeness']=='complete'
    assert rows[0]['value_usd']=='1250' and rows[0]['shares']=='100'
    assert compare_holdings(snapshot(positions=rows),None,{})['items'][0]['symbol'] is None
    old,old_rows=parse_13f(cover,[table],make_ref(filed_at='2023-01-02'),received_at='2026-10-12T00:00:00Z')
    assert old['completeness']=='unsupported' and old_rows==[]
    supported,supported_rows=parse_13f(cover,[table],make_ref(filed_at='2023-01-03'),received_at='2026-10-12T00:00:00Z')
    assert supported_rows[0]['value_usd']=='1250'
    put=table.replace(b'<investmentDiscretion>',b'<putCall>PUT</putCall><investmentDiscretion>')
    prn=table.replace(b'<sshPrnamtType>SH',b'<sshPrnamtType>PRN')
    assert parse_13f(cover,[put],make_ref(),received_at='2026-10-12T00:00:00Z')[1][0]['row_key']!=rows[0]['row_key']
    assert parse_13f(cover,[prn],make_ref(),received_at='2026-10-12T00:00:00Z')[1][0]['row_key']!=rows[0]['row_key']
    repeated=table.replace(b'037833100',b'67066G104')
    assert parse_13f(cover,[repeated],make_ref(),received_at='2026-10-12T00:00:00Z')[1][0]['shares']=='300'
    bad=cover.replace(b'<isConfidentialOmitted>false',b'<isConfidentialOmitted>true')
    assert parse_13f(bad,[table],make_ref(),received_at='2026-10-12T00:00:00Z')[0]['completeness']=='incomplete'
    bad=cover.replace(b'<tableEntryTotal>2',b'<tableEntryTotal>3')
    assert parse_13f(bad,[table],make_ref(),received_at='2026-10-12T00:00:00Z')[0]['completeness']=='incomplete'
    for name,kind in [('13f_new_holdings.xml','new_holdings'),('13f_restatement.xml','restatement')]:
        assert parse_13f((FIXTURES/name).read_bytes(),[table],make_ref(form='13F-HR/A'),received_at='2026-10-12T00:00:00Z')[0]['amendment_type']==kind


def test_xml_rejects_entities_and_nonfinite_values():
    from app.sec_xml import parse_xml
    from app.sec_13f import parse_13f
    evil='<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]><x>&x;</x>'
    for value in [evil.encode(),evil.encode('utf-16'),b'<x>'+b'a'*(20*1024*1024)+b'</x>']:
        with pytest.raises(ValueError):parse_xml(value)
    with pytest.raises(ValueError):
        parse_13f((FIXTURES/'13f_cover.xml').read_bytes(),[(FIXTURES/'13f_table.xml').read_bytes().replace(b'<value>1250',b'<value>NaN')],make_ref(),received_at='2026-10-12T00:00:00Z')
