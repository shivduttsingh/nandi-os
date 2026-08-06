from __future__ import annotations

from datetime import datetime, timezone

from nandi_v2.engine import decide, option_activity, strike_evidence_rows
from nandi_v2.models import DecisionAction, MarketContext, OptionChainSnapshot, OptionLeg, StrikeRow


def _snapshot(direction: str) -> tuple[OptionChainSnapshot, MarketContext]:
    now = datetime.now(timezone.utc)
    spot = 24655.0 if direction == "bullish" else 24605.0
    rows = []
    for strike in range(24350, 24901, 50):
        if direction == "bullish":
            pe = OptionLeg(35.0, -7.0 if strike <= 24650 else -2.0, 700_000 if strike == 24650 else 260_000, 150_000 if strike <= 24650 else 20_000, 420_000 if strike <= 24650 else 120_000, 12.0)
            ce = OptionLeg(55.0, 14.0 if strike <= 24700 else 5.0, 500_000 if strike == 24700 else 240_000, -110_000 if strike <= 24700 else -20_000, 520_000 if strike <= 24700 else 180_000, 12.0)
        else:
            ce = OptionLeg(42.0, -8.0 if strike >= 24600 else -2.0, 720_000 if strike == 24650 else 280_000, 160_000 if strike >= 24600 else 20_000, 450_000 if strike >= 24600 else 130_000, 13.0)
            pe = OptionLeg(58.0, 16.0 if strike >= 24550 else 6.0, 480_000 if strike == 24550 else 230_000, -120_000 if strike >= 24550 else -20_000, 560_000 if strike >= 24550 else 180_000, 13.0)
        rows.append(StrikeRow(float(strike), ce, pe))
    snapshot = OptionChainSnapshot(now, "13-Aug-2026", spot, tuple(rows))
    context = MarketContext(now, 24643.0 if direction == "bullish" else 24618.0, 24650.0 if direction == "bullish" else 24655.0, 24610.0, 64.0 if direction == "bullish" else 36.0)
    return snapshot, context


def test_option_activity_classification() -> None:
    assert option_activity(OptionLeg(change=-2, change_oi=10)) == "WRITING"
    assert option_activity(OptionLeg(change=2, change_oi=10)) == "LONG BUILDUP"
    assert option_activity(OptionLeg(change=2, change_oi=-10)) == "SHORT COVERING"
    assert option_activity(OptionLeg(change=-2, change_oi=-10)) == "LONG UNWINDING"


def test_bullish_setup_returns_buy_ce() -> None:
    snapshot, context = _snapshot("bullish")
    decision = decide(snapshot, context)
    assert decision.action == DecisionAction.BUY_CE
    assert decision.ce_score >= 75
    assert decision.levels.stop < snapshot.spot < decision.levels.target_1


def test_bearish_setup_returns_buy_pe() -> None:
    snapshot, context = _snapshot("bearish")
    decision = decide(snapshot, context)
    assert decision.action == DecisionAction.BUY_PE
    assert decision.pe_score >= 75
    assert decision.levels.target_1 < snapshot.spot < decision.levels.stop


def test_conflicting_setup_returns_no_trade() -> None:
    now = datetime.now(timezone.utc)
    leg = OptionLeg(40.0, 0.0, 300_000, 0.0, 100_000, 12.0)
    rows = tuple(StrikeRow(float(strike), leg, leg) for strike in range(24400, 24951, 50))
    decision = decide(OptionChainSnapshot(now, "13-Aug-2026", 24650.0, rows), MarketContext(now, 24650.0, 24670.0, 24630.0, 50.0))
    assert decision.action == DecisionAction.NO_TRADE
    assert decision.blockers


def test_evidence_table_is_atm_plus_minus_five() -> None:
    snapshot, _ = _snapshot("bullish")
    rows = strike_evidence_rows(snapshot)
    assert len(rows) == 11
    assert sum(1 for row in rows if row["ATM"] == "ATM") == 1
