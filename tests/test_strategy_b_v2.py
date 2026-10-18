from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from strategy_b.v2_backtest import PROFILES, _evaluate_trade, _trigger_entry


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


def test_v2_trigger_waits_for_break_above_signal_option_high():
    profile = PROFILES[0]
    base = datetime(2026, 5, 5, 10, 0)
    signal = candle(base, 100, 105, 99, 104)
    series = (
        signal,
        candle(base + timedelta(minutes=1), 104, 105.05, 103, 104.5),
        candle(base + timedelta(minutes=2), 104.5, 106, 104, 105.8),
    )
    result = _trigger_entry(series, [c.timestamp for c in series], base, signal, profile, 0.20)
    assert result is not None
    index, entry = result
    assert index == 2
    assert round(entry, 2) == 105.30


def test_v2_ambiguous_entry_bar_is_counted_as_loss():
    base = datetime(2026, 5, 5, 10, 0)
    series = (
        candle(base, 100, 112, 93, 104),
        candle(base + timedelta(minutes=1), 104, 108, 102, 106),
    )
    outcome, net, held = _evaluate_trade(
        series,
        0,
        entry=100.0,
        stop_distance=5.0,
        target_distance=10.0,
        max_hold_minutes=20,
        friction_points=0.50,
    )
    assert outcome == "LOSS"
    assert net == -5.50
    assert held == 0.0


def test_v2_target_hit_returns_positive_reward_after_friction():
    base = datetime(2026, 5, 5, 10, 0)
    series = (
        candle(base, 100, 106, 98, 104),
        candle(base + timedelta(minutes=1), 104, 111, 102, 110),
    )
    outcome, net, _ = _evaluate_trade(
        series,
        0,
        entry=100.0,
        stop_distance=5.0,
        target_distance=10.0,
        max_hold_minutes=20,
        friction_points=0.50,
    )
    assert outcome == "WIN"
    assert net == 9.50
