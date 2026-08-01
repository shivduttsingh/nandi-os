from datetime import datetime

from nandi_oi.evidence import decision_history_rows, live_evidence
from nandi_oi.models import Decision, OptionLeg, OptionSnapshot


def test_live_evidence_explains_oi_premium_and_market_structure():
    snapshot = OptionSnapshot(
        timestamp=datetime(2026, 8, 1, 10, 0),
        spot=25020,
        spot_change=12,
        recent_high=25010,
        recent_low=24980,
        legs=(
            OptionLeg(25000, "CE", 1000, -50, 110, 5, 500, 109, 111),
            OptionLeg(25000, "PE", 1000, 50, 90, -4, 500, 89, 91),
        ),
        expiry="2026-08-06",
    )
    decision = Decision("BUY CE", 85, 20, 85, True, 25000)

    evidence = live_evidence(snapshot, decision)

    assert evidence["oi"][0]["Activity"] == "SHORT COVERING"
    assert "directional" in evidence["oi"][0]["Explanation"]
    assert evidence["premium"][0]["Contract"] == "ATM CE"
    assert evidence["structure"][0]["Structure"] == "Upward breakout"
    assert evidence["score"][0]["Score"] == 85


def test_decision_history_keeps_chart_fields_and_does_not_mutate_input():
    history = [{"time": "2026-08-01T10:00:00", "spot": 25000, "bullish": 81,
                "bearish": 30, "decision": "BUY CE", "confidence": 81}]
    rows = decision_history_rows(history)

    assert rows == history
    assert rows[0] is not history[0]
