"""Optional SDK history adapter; never substitutes synthetic bars."""
import asyncio
import time
from .connectors import _epoch, _enum_name, lb_config
from .market import MarketError, normalize_bars


def decode_candlesticks(rows, instrument, interval, now):
    values = []
    for row in rows:
        session = {'intraday':'regular', 'pre':'pre', 'post':'post', 'overnight':'overnight'}.get(
            _enum_name(getattr(row, 'trade_session', None)), 'unknown')
        if interval in ('1d', '1wk'):
            session = 'regular'
        values.append({'time':_epoch(row.timestamp), **{k:getattr(row, k, None) for k in
            ('open','high','low','close','volume')}, 'session':session, 'closed_at':None})
    return normalize_bars(values, interval, instrument=instrument, now=now)


async def longbridge_candles(settings, instrument, interval, session):
    if not instrument.provider_symbols.get('longbridge'):
        raise MarketError('该标的没有长桥历史行情映射')
    if session == 'overnight' and not settings.lb_overnight_enabled:
        raise MarketError('夜盘已关闭，请先启用长桥夜盘配置')
    try:
        from longbridge.openapi import QuoteContext, Period, AdjustType, TradeSessions
    except ImportError as exc:
        raise MarketError('请安装 requirements-longbridge.txt 后使用长桥行情') from exc
    def fetch():
        # Capture credentials and overnight option from this request's settings.
        ctx = QuoteContext(lb_config(settings))
        period = getattr(Period, {'5m':'Min_5','1h':'Min_60','1d':'Day','1wk':'Week'}[interval])
        sessions = TradeSessions.All if interval in ('5m','1h') else TradeSessions.Intraday
        return ctx.candlesticks(instrument.provider_symbols['longbridge'], period, 1000,
                                AdjustType.NoAdjust, sessions)
    try:
        rows = await asyncio.to_thread(fetch)
        bars = decode_candlesticks(rows, instrument, interval, time.time())
    except Exception as exc:
        raise MarketError('长桥历史行情不可用，请核查 SDK、授权和行情权限') from exc
    if interval in ('5m','1h'):
        if not settings.lb_overnight_enabled:
            bars = [b for b in bars if b['session'] != 'overnight']
        if session != 'all':
            bars = [b for b in bars if b['session'] == session]
    return {'bars':bars, 'provider':'longbridge', 'source':'Longbridge OpenAPI · 历史行情',
            'name':instrument.name, 'currency':instrument.currency, 'exchange':instrument.market,
            'price':bars[-1]['close'] if bars else None, 'quote_time':bars[-1]['time'] if bars else None,
            'is_demo':False, 'adjustment':'供应商原始 OHLC；日/周线常规时段',
            'notice':'最近最多 1000 根 K 线，受账户权限和供应商历史范围限制；时段依据返回标签。缺少当天实际闭市元数据，收盘时间标为未知；分钟线仅在名义周期结束后保守判定完成，末根信号可能延后。'}
