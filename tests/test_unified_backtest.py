from datetime import datetime, timedelta

from nandi_oi.models import OptionLeg, OptionSnapshot
from nandi_oi.unified_backtest import UnifiedBacktester


def snapshot(at: datetime, spot: float, premium: float) -> OptionSnapshot:
    legs = []
    for strike in range(24750, 25300, 50):
        legs.extend((
            OptionLeg(strike, "CE", 1000, -100, premium, 5, 1000, premium - 1, premium + 1,
                      premium, premium * 1.35, premium * 0.95),
            OptionLeg(strike, "PE", 1000, 100, premium, -5, 1000, premium - 1, premium + 1,
                      premium, premium * 1.05, premium * 0.95),
        ))
    return OptionSnapshot(at, spot, 10, spot - 1, spot - 20, tuple(legs), "2026-07-23")


def test_unified_backtest_runs_oi_and_every_saved_rsi_variant():
    start = datetime(2026, 7, 20, 9, 15)
    weekly = [snapshot(start + timedelta(minutes=5 * index), 25000 + index * 10, 100 + index * 10)
              for index in range(20)]
    monthly = [snapshot(start + timedelta(minutes=5 * index), 25000 - index * 10, 100 + index * 2)
               for index in range(20)]
    result = UnifiedBacktester(rsi_timeframes=(1, 5)).run(
        weekly, monthly,
        {
            "RSI 5 — 30/70": {"length": 5, "lower": 30, "upper": 70},
            "RSI 6 — 25/75": {"length": 6, "lower": 25, "upper": 75},
        },
        one_minute_closes={start + timedelta(minutes=index): 100 + index for index in range(30)},
    )
    assert [(run.strategy, run.contract) for run in result.runs] == [
        ("OI flow", "Nearest weekly evidence check"),
        ("NIFTY price structure", "Nearest weekly evidence check"),
        ("OI-wall movement", "Nearest weekly evidence check"),
        ("Option premium and liquidity", "Nearest weekly evidence check"),
        ("Three-snapshot OI persistence", "Nearest weekly evidence check"),
        ("Nandi OI V1", "Nearest weekly"),
        ("RSI 5 — 30/70", "Nearest weekly"),
        ("RSI 5 — 30/70", "Nearest monthly"),
        ("RSI 6 — 25/75", "Nearest weekly"),
        ("RSI 6 — 25/75", "Nearest monthly"),
    ]
    assert len(result.rsi_touch_rows()) == 4
    assert {row["Strategy"] for row in result.summary_rows()} == {
        "OI flow", "NIFTY price structure", "OI-wall movement",
        "Option premium and liquidity", "Three-snapshot OI persistence",
        "Nandi OI V1", "RSI 5 — 30/70", "RSI 6 — 25/75",
    }


def test_unified_backtest_rejects_empty_strategy_selection():
    start = datetime(2026, 7, 20, 9, 15)
    records = [snapshot(start + timedelta(minutes=5 * index), 25000 + index, 100) for index in range(5)]
    try:
        UnifiedBacktester().run(records, records, {})
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("Expected an empty strategy selection to be rejected")
