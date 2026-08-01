from datetime import datetime, timedelta

from nandi_oi.engine import EngineConfig, NandiOIEngine
from nandi_oi.models import OptionLeg, OptionSnapshot
from nandi_oi.rsi_backtest import RsiLevelBacktester


def _bullish_snapshot(timestamp: datetime) -> OptionSnapshot:
    legs = []
    for strike in range(24750, 25300, 50):
        legs.extend((
            OptionLeg(strike, "CE", 1000, -100, 100, 5, 1000, 99, 101),
            OptionLeg(strike, "PE", 1000, 100, 90, -5, 1000, 89, 91),
        ))
    return OptionSnapshot(
        timestamp, 25000, 10, 24990, 24950, tuple(legs), "2026-08-06",
    )


def test_oi_v1_thresholds_and_persistent_buy_ce_signal_are_unchanged():
    config = EngineConfig()
    assert (config.approval_score, config.minimum_lead, config.persistence_snapshots) == (80.0, 20.0, 3)

    engine = NandiOIEngine()
    start = datetime(2026, 8, 1, 9, 15)
    decisions = [
        engine.add_snapshot(_bullish_snapshot(start + timedelta(minutes=index * 5)))
        for index in range(3)
    ]
    assert [decision.action for decision in decisions[:2]] == ["NO TRADE", "NO TRADE"]
    assert decisions[-1].action == "BUY CE"


def test_rsi_strategy_keeps_five_percent_premium_stop():
    strategy = RsiLevelBacktester(length=14, lower=24, upper=72)
    assert strategy.stop_pct == 0.05
    assert (strategy.length, strategy.lower, strategy.upper) == (14, 24.0, 72.0)
