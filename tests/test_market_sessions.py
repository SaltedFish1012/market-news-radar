import importlib.util
import pytest


def model():
    assert importlib.util.find_spec('app.market_sessions'), 'session quote model is missing'
    from app import market_sessions
    return market_sessions


@pytest.mark.parametrize('upstream,received,want', [(1000,1120,True),(1000,1120.01,False),
    (1005,1000,True),(1005.01,1000,False),(None,1000,False)])
def test_quote_freshness_boundaries(upstream, received, want):
    m = model()
    quote = m.Quote('NVDA',100,'USD','Longbridge OpenAPI','pre',upstream,received,'normal')
    assert m.quote_is_eligible(quote) is want
    if upstream == 1000:
        assert m.quote_view(quote, 1201)['fresh'] is False


@pytest.mark.parametrize('price,session,status', [(float('nan'),'regular','normal'),
    (float('inf'),'regular','normal'),(-1,'regular','normal'),(100,'unknown','normal'),(100,'regular','halted')])
def test_unusable_quote_never_eligible(price, session, status):
    m = model()
    assert not m.quote_is_eligible(m.Quote('NVDA',price,'USD','test',session,1000,1001,status))


def test_reference_close_has_no_future_data():
    m = model()
    bars = [{'session':'regular','complete':True,'close':100,'closed_at':900},
            {'session':'regular','complete':True,'close':110,'closed_at':1100},
            {'session':'post','complete':True,'close':90,'closed_at':950},
            {'session':'regular','complete':False,'close':80,'closed_at':980}]
    assert m.reference_close(bars,1000) == {'price':100,'closed_at':900}
    assert m.reference_close(bars,800) is None
