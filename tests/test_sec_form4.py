from pathlib import Path
import pytest
from sec_helpers import make_ref

FIXTURES=Path(__file__).parent/'fixtures'/'sec'


def parse(data=None,form='4'):
    from app.sec_form4 import parse_form4
    return parse_form4(data or (FIXTURES/'form4.xml').read_bytes(),make_ref(form=form,report_period='2026-08-12'),received_at='2026-10-12T00:00:00Z')


def test_form4_shared_owners_price_footnote_and_amendment():
    filing,rows=parse((FIXTURES/'form4_amendment.xml').read_bytes(),form='4/A')
    assert filing['form']=='4/A' and filing['amendment_type']=='unknown'
    assert filing['report_period']=='2026-08-12'
    assert len(rows)==1 and len(rows[0]['owners'])==2
    assert rows[0]['ownership']=='I' and rows[0]['ownership_nature']=='Shared trust'
    assert rows[0]['amount_usd'] is None and rows[0]['footnotes']['F1']


def test_transaction_codes_and_exact_amount():
    from app.sec_form4 import transaction_kind
    assert [transaction_kind(c) for c in ['P','S','A','M','F','G','J']]==['buy','sell','grant','exercise','tax','gift','other']
    assert parse()[1][0]['amount_usd']=='1250.00'
    raw=(FIXTURES/'form4.xml').read_bytes()
    for value in [b'12-13',b'',b'NaN']:
        rows=parse(raw.replace(b'<value>12.50',b'<value>'+value))[1]
        assert rows[0]['amount_usd'] is None
    rows=parse(raw.replace(b'<value>12.50</value>',b'<value>12.50</value><footnoteId id="F1"/>'))[1]
    assert rows[0]['amount_usd'] is None


def test_derivative_rows_and_ten_b5_are_distinct():
    filing,rows=parse()
    assert len(rows)==2 and [r['derivative'] for r in rows]==[False,True]
    assert rows[0]['row_key']!=rows[1]['row_key']
    assert rows[0]['owners'][0]['is_director'] is True
    assert rows[0]['owners'][1]['officer_title']=='CFO'
    assert rows[0]['ten_b5_1'] is True and rows[1]['code']=='M'
    assert rows[1]['ownership']=='I'
    raw=(FIXTURES/'form4.xml').read_bytes().replace(b'<aff10b5One>1</aff10b5One>',b'')
    assert parse(raw)[1][0]['ten_b5_1'] is None
    with pytest.raises(ValueError):parse(raw.replace(b'0001067983',b'0000000042'))
