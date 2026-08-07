from datetime import datetime, timezone

from nandi_v2.email_alerts import is_entry_alert
from nandi_v2.models import Decision, DecisionAction, ScoreBreakdown, TradeLevels


def _decision(action: DecisionAction, score: float) -> Decision:
    breakdown = ScoreBreakdown(10, 10, 10, 10, 10, 10, 5, 5)
    now = datetime.now(timezone.utc)
    return Decision(action=action, score=score, ce_score=score, pe_score=20, selected_strike=24650, market_state="BULLISH TREND", breakdown=breakdown, opposite_breakdown=breakdown, levels=TradeLevels(), generated_at=now, data_timestamp=now)


def test_email_alert_requires_confirmed_buy_and_score_80() -> None:
    assert is_entry_alert(_decision(DecisionAction.BUY_CE, 80))
    assert not is_entry_alert(_decision(DecisionAction.PREPARE_CE, 90))
    assert not is_entry_alert(_decision(DecisionAction.BUY_PE, 79.9))
