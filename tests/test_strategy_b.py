from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from strategy_b.engine import StrategyBSignal, assess_strategy_b
from strategy_b.public_backtest import _trade_outcome


def candle(ts, open_, high, low, close, volume=100.0, oi=1000.0):
    return IntradayCandle(
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        open_interest=oi,
    )


def test_strategy_b_requires_synchronized_history():
    ts = datetime(2026, 6, 10, 10, 0)
    one = [candle(ts, 100, 101, 99, 100.5)]
    result = assess_strategy_b(one, one, one, one, one)
    assert result.signal == StrategyBSignal.UNAVAILABLE


def test_strategy_b_can_confirm_clean_ce_alignment():
    base = datetime(2026, 6, 10, 10, 0)
    n1 = []
    ce = []
    pe = []
    for i in range(12):
        ts = base + timedelta(minutes=i)
        spot_close = 25000 + i * 0.5
        n1.append(candle(ts, spot_close - 0.2, spot_close + 2.0, spot_close - 2.0, spot_close, 100 + i * 5, 0))
        ce_close = 100 + i * 1.5
        ce.append(candle(ts, ce_close - 0.5, ce_close + 1.0, ce_close - 1.0, ce_close, 100 + i * 20, 1000 + i * 25))
        pe_close = 120 - i * 1.0
        pe.append(candle(ts, pe_close + 0.4, pe_close + 1.0, pe_close - 1.0, pe_close, 100 + i * 5, 1000 + i * 20))

    n5 = [
        candle(base + timedelta(minutes=5 * i), 25000 + i, 25004 + i, 24998 + i, 25002 + i)
        for i in range(5)
    ]
    n15 = [
        candle(base + timedelta(minutes=15 * i), 25000 + i, 25006 + i, 24996 + i, 25003 + i)
        for i in range(4)
    ]

    result = assess_strategy_b(n1, n5, n15, ce, pe, trade_threshold=80)
    assert result.direction == "CE"
    assert result.score > result.opposite_score
    assert result.signal in {StrategyBSignal.TRADE_CE, StrategyBSignal.BLOCKED_CE}


def test_option_backtest_enters_at_open_and_counts_ambiguous_bar_as_loss():
    base = datetime(2026, 6, 10, 10, 0)
    series = (
        candle(base, 100, 112, 94, 104),
        candle(base + timedelta(minutes=1), 104, 116, 97, 110),
    )
    outcome, entry, exit_price, net, held = _trade_outcome(
        series,
        0,
        target_points=10,
        stop_points=5,
        max_hold_minutes=15,
        entry_slippage_points=0.25,
        friction_points=0.50,
    )
    assert outcome == "LOSS"
    assert entry == 100.25
    assert exit_price == 95.25
    assert net == -5.50
    assert held == 0.0


def test_option_backtest_target_win_is_positive_after_friction():
    base = datetime(2026, 6, 10, 10, 0)
    series = (
        candle(base, 100, 105, 99, 103),
        candle(base + timedelta(minutes=1), 103, 112, 102, 110),
    )
    outcome, _, _, net, _ = _trade_outcome(
        series,
        0,
        target_points=10,
        stop_points=5,
        max_hold_minutes=15,
        entry_slippage_points=0.25,
        friction_points=0.50,
    )
    assert outcome == "WIN"
    assert net == 9.50
