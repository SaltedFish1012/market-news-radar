"""Hand-authored offline SEC fixtures; never represent live disclosures."""


def make_ref(**overrides):
    return dict(accession='0001067983-26-000001',cik='1067983',form='13F-HR',filed_at='2026-08-14',
        report_period='2026-06-30',primary_document='primary_doc.xml',
        source_url='https://www.sec.gov/Archives/edgar/data/1067983/000106798326000001/primary_doc.xml',**{}) | overrides


def make_filing(**overrides):
    return make_ref() | dict(entity_name='Offline institution',received_at='2026-10-12T00:00:00+00:00',
        amendment_type='none',completeness='complete',detail='') | overrides


def make_position(**overrides):
    return dict(row_key='["67066G104","COM","","SH"]',issuer_name='Offline issuer',security_class='COM',
        cusip='67066G104',shares='100',value_usd='1250',share_type='SH',put_call='') | overrides


def make_transaction(**overrides):
    return dict(row_key='non_derivative:0',issuer_cik='1067983',issuer_name='Offline issuer',
        security_title='Common Stock',owners=[dict(cik='1',name='Offline owner',is_director=True,
        is_officer=False,is_ten_percent_owner=False,is_other=False,officer_title='',other_text='')],
        derivative=False,transaction_date='2026-08-12',code='P',acquired_disposed='A',shares='100',
        price='12.50',amount_usd='1250.00',post_shares='200',ownership='D',ownership_nature='',
        footnotes={},ten_b5_1=None) | overrides


def make_progress(**overrides):
    return dict(source_key='institution:1067983',initialized=True,symbols=[],pending=[],last_success_at=None,
        last_error='',retry_at=0,failures=0,coverage_start='2026-03-31',coverage_end='2026-06-30',
        coverage_state='complete') | overrides
