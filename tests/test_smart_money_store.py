import sqlite3
import pytest
from app.db import DB
from sec_helpers import make_filing,make_position,make_progress,make_transaction
from test_x_storage import event


@pytest.fixture
def db(tmp_path):
    db=DB(str(tmp_path/'test.db'))
    yield db
    db.conn.close()


def test_atomic_filing_and_progress(db):
    from app.smart_money_store import SmartMoneyStore
    store=SmartMoneyStore(db);filing=make_filing();progress=make_progress(last_success_at=1000)
    assert store.commit_filing(filing,[make_position()],[],progress) is True
    assert store.commit_filing(filing,[make_position()],[],progress) is False
    assert len(store.positions(filing['accession']))==1
    assert store.progress(progress['source_key'])['last_success_at']==1000
    assert store.filings(cik='1067983',kind='institution')==[filing]
    assert not db.list_rows('news') and not db.due_alerts()


@pytest.mark.parametrize('invalid',['number','duplicate','database'])
def test_invalid_row_rolls_back_filing_and_progress(db,invalid):
    from app.smart_money_store import SmartMoneyStore
    store=SmartMoneyStore(db);position=make_position()
    positions=[position]
    if invalid=='number':positions=[make_position(shares='NaN')]
    if invalid=='duplicate':positions.append(position)
    if invalid=='database':db.conn.execute("CREATE TRIGGER reject_sec BEFORE INSERT ON institution_positions BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises((ValueError,sqlite3.IntegrityError)):
        store.commit_filing(make_filing(),positions,[],make_progress(last_success_at=1000))
    assert not store.has_filing('0001067983-26-000001')
    assert store.progress('institution:1067983')['last_success_at'] is None


def test_migration_is_repeatable_and_preserves_news(tmp_path):
    from app.smart_money_store import SmartMoneyStore
    path=str(tmp_path/'test.db');db=DB(path);db.store_x_event(event())
    before=[tuple(r) for r in db.conn.execute('SELECT * FROM news')];db.conn.close()
    for _ in range(2):
        db=DB(path)
        assert [tuple(r) for r in db.conn.execute('SELECT * FROM news')]==before
        assert SmartMoneyStore(db).filings()==[]
        db.conn.close()


def test_transaction_queries_keep_sources_and_filter_before_limit(db):
    from app.smart_money_store import SmartMoneyStore
    store=SmartMoneyStore(db)
    rows=[make_transaction(row_key=f'non_derivative:{i}',code='P' if i<2 else 'S') for i in range(3)]
    store.commit_filing(make_filing(form='4'),[],rows,make_progress(source_key='issuer:1067983',symbols=['NVDA']))
    items,total=store.transactions(code='P',limit=1)
    assert total==2 and len(items)==1 and items[0]['form']=='4'
    assert items[0]['source_url'].startswith('https://www.sec.gov/Archives/')
    assert store.all_progress()[0]['symbols']==['NVDA']
