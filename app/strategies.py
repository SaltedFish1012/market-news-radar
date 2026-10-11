"""Transparent long-only research rules; next-bar execution with explicit costs."""
from __future__ import annotations
import math

CATALOG = [
    {'id': 'ema_cross', 'name': '双均线趋势', 'tag': '趋势跟随', 'description': 'EMA20 高于 EMA50 时持有，低于时退出。', 'risk': '震荡区间可能频繁切换。'},
    {'id': 'rsi_reversion', 'name': 'RSI 均值回归', 'tag': '超卖修复', 'description': 'RSI14 ≤ 30 进入，RSI14 ≥ 55 退出。', 'risk': '强下跌趋势中超卖可持续很久。'},
    {'id': 'donchian', 'name': '通道突破', 'tag': '动量突破', 'description': '收盘突破前 20 根最高价进入，跌破前 10 根最低价退出。', 'risk': '跳空与假突破会放大损失。'},
    {'id': 'macd', 'name': 'MACD 趋势', 'tag': '动量跟随', 'description': 'MACD(12,26) 高于 Signal9 时持有，低于时退出。', 'risk': '指标滞后，低波动行情可能反复触发。'},
    {'id': 'bollinger', 'name': '布林带回归', 'tag': '均值回归', 'description': '收盘低于 20 根布林下轨进入，回到中轨退出（2 倍标准差）。', 'risk': '价格可能沿下轨持续下跌。'},
]


def sma(values, period):
    return [sum(values[i-period+1:i+1])/period if i >= period-1 else None for i in range(len(values))]


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    out[period-1] = sum(values[:period]) / period
    alpha = 2 / (period+1)
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1-alpha) * out[i-1]
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [values[i]-values[i-1] for i in range(1, len(values))]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    def value():
        return 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100/(1+gain/loss))
    out[period] = value()
    for i in range(period+1, len(values)):
        gain = (gain*(period-1) + max(changes[i-1], 0)) / period
        loss = (loss*(period-1) + max(-changes[i-1], 0)) / period
        out[i] = value()
    return out


def indicators(bars):
    closes = [b['close'] for b in bars]
    e12, e26 = ema(closes, 12), ema(closes, 26)
    macd = [a-b if a is not None and b is not None else None for a, b in zip(e12, e26)]
    valid = [m for m in macd if m is not None]
    signal = [None] * (len(macd)-len(valid)) + ema(valid, 9)
    mid = sma(closes, 20)
    deviation = [math.sqrt(sum((v-mid[i])**2 for v in closes[i-19:i+1])/20) if mid[i] is not None else None
                 for i in range(len(closes))]
    return {'ema20': ema(closes, 20), 'ema50': ema(closes, 50), 'rsi14': rsi(closes),
            'macd': macd, 'macd_signal': signal,
            'boll_mid': mid, 'boll_upper': [m+2*s if m is not None else None for m, s in zip(mid, deviation)],
            'boll_lower': [m-2*s if m is not None else None for m, s in zip(mid, deviation)]}


def targets(bars, ind, strategy):
    held, out = False, []
    for i, b in enumerate(bars):
        if strategy == 'ema_cross' and ind['ema50'][i] is not None:
            held = ind['ema20'][i] > ind['ema50'][i]
        elif strategy == 'macd' and ind['macd_signal'][i] is not None:
            held = ind['macd'][i] > ind['macd_signal'][i]
        elif strategy == 'rsi_reversion' and ind['rsi14'][i] is not None:
            if ind['rsi14'][i] <= 30:
                held = True
            elif ind['rsi14'][i] >= 55:
                held = False
        elif strategy == 'donchian' and i >= 20:
            if b['close'] > max(row['high'] for row in bars[i-20:i]):
                held = True
            elif b['close'] < min(row['low'] for row in bars[i-10:i]):
                held = False
        elif strategy == 'bollinger' and ind['boll_mid'][i] is not None:
            if b['close'] < ind['boll_lower'][i]:
                held = True
            elif b['close'] >= ind['boll_mid'][i]:
                held = False
        out.append(held)
    return out


def strategy_snapshot(bars):
    completed = [b for b in bars if b.get('complete', True)]
    if len(completed) < 60:
        return {'items': [{**s, 'state': 'insufficient', 'label': '数据不足'} for s in CATALOG], 'as_of': None}
    ind = indicators(completed)
    out = []
    for rule in CATALOG:
        target = targets(completed, ind, rule['id'])
        state = 'enter' if target[-1] and not target[-2] else 'exit' if not target[-1] and target[-2] else 'hold' if target[-1] else 'flat'
        out.append({**rule, 'state': state, 'label': {'enter': '新入场信号', 'exit': '离场信号', 'hold': '持有区间', 'flat': '观望区间'}[state]})
    return {'items': out, 'as_of': completed[-1]['time'],
            'rsi14': ind['rsi14'][-1], 'ema20': ind['ema20'][-1], 'ema50': ind['ema50'][-1]}


def backtest(bars, strategy, initial_cash=10000.0, fee_bps=5.0, slippage_bps=5.0):
    if strategy not in {s['id'] for s in CATALOG}:
        raise ValueError('未知策略')
    completed = [b for b in bars if b.get('complete', True)]
    if len(completed) < 80:
        raise ValueError('至少需要 80 根已收盘 K 线，请选择更长周期')
    ind = indicators(completed)
    desired = targets(completed, ind, strategy)
    fee, slip = fee_bps/10000, slippage_bps/10000
    start = 60  # same evaluation window for every strategy; warmup is not included in returns
    cash, shares, entry = initial_cash, 0.0, None
    equity, trades, executions = [], [], []
    peak, drawdown, occupied = initial_cash, 0.0, 0
    baseline_shares = initial_cash/(completed[start]['open']*(1+slip)*(1+fee))
    for i in range(start, len(completed)):
        b = completed[i]
        # Only the previous CLOSED bar decides today's OPEN fill.
        want = desired[i-1]
        if want and shares == 0:
            price = b['open']*(1+slip)
            cost = cash
            shares = cash/(price*(1+fee))
            cash = 0.0
            entry = {'time': b['time'], 'signal_time': completed[i-1]['time'], 'price': price, 'cost': cost}
            executions.append({'time': b['time'], 'signal_time': completed[i-1]['time'], 'side': 'buy', 'price': price})
        elif not want and shares > 0:
            price = b['open']*(1-slip)
            cash = shares*price*(1-fee)
            trades.append({'entry_time': entry['time'], 'exit_time': b['time'],
                           'entry_price': entry['price'], 'exit_price': price,
                           'pnl': cash-entry['cost'], 'return_pct': (cash/entry['cost']-1)*100})
            executions.append({'time': b['time'], 'signal_time': completed[i-1]['time'], 'side': 'sell', 'price': price})
            shares, entry = 0.0, None
        value = cash+shares*b['close']
        peak = max(peak, value)
        drawdown = max(drawdown, (peak-value)/peak)
        occupied += int(shares > 0)
        # Compare marked-to-market positions consistently; no invented final exit.
        equity.append({'time': b['time'], 'equity': round(value, 4),
                       'benchmark': round(baseline_shares*b['close'], 4)})
    final = equity[-1]['equity']
    return {'strategy': strategy, 'initial_cash': initial_cash, 'final_equity': final,
            'total_return_pct': (final/initial_cash-1)*100, 'max_drawdown_pct': drawdown*100,
            'benchmark_return_pct': (equity[-1]['benchmark']/initial_cash-1)*100,
            'trade_count': len(trades), 'win_rate_pct': sum(t['pnl']>0 for t in trades)/len(trades)*100 if trades else None,
            'exposure_pct': occupied/len(equity)*100, 'open_position': shares > 0,
            'open_position_value': shares*completed[-1]['close'], 'fee_bps': fee_bps, 'slippage_bps': slippage_bps,
            'start_time': completed[start]['time'], 'end_time': completed[-1]['time'],
            'bar_count': len(completed), 'equity': equity, 'trades': trades[-100:], 'executions': executions,
            'assumptions': '仅做多、单股全仓、允许碎股、不加杠杆；前一根收盘信号→下一根开盘成交；每边计手续费与滑点。前60根用于预热，不计收益。仅使用已收盘K线，期末未平仓按收盘价估值；未计税、融资和成交容量。'}
