from datetime import datetime, timedelta

from nandi_oi.evidence_backtest import EvidenceBacktester
from nandi_oi.models import OptionLeg, OptionSnapshot


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


def test_evidence_backtest_replays_each_oi_component_independently():
    start = datetime(2026, 7, 20, 9, 15)
    snapshots = [snapshot(start + timedelta(minutes=5 * index), 25000 + index * 10, 100 + index * 10)
                 for index in range(12)]
    result = EvidenceBacktester().run(snapshots)
    assert [item.name for item in result.runs] == [
        "OI flow", "NIFTY price structure", "OI-wall movement",
        "Option premium and liquidity", "Three-snapshot OI persistence",
    ]
    assert all(item.result.start_date == start.date() for item in result.runs)
    assert result.runs[0].result.trades


def test_evidence_backtest_rejects_empty_history():
    try:
        EvidenceBacktester().run([])
    except ValueError as exc:
        assert "No historical snapshots" in str(exc)
    else:
        raise AssertionError("Expected empty evidence history to be rejected")
