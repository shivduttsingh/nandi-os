from dataclasses import replace
from datetime import datetime, timezone

from nandi_v2.confluence import apply_confluence_gate, combine_decision
from nandi_v2.models import Decision, DecisionAction, ScoreBreakdown, TradeLevels
from nandi_v2.technical import TechnicalAssessment, TechnicalDirection


NOW = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)


def base(action: DecisionAction = DecisionAction.BUY_CE) -> Decision:
    score = ScoreBreakdown(20, 18, 13, 14, 9, 8, 5, 5)
    return Decision(
        action=action,
        score=92.0,
        ce_score=92.0,
        pe_score=30.0,
        selected_strike=25000.0,
        market_state="BULLISH BREAKOUT",
        breakdown=score,
        opposite_breakdown=score,
        levels=TradeLevels(entry=25000, stop=24970, target_1=25050, target_2=25100),
        generated_at=NOW,
        data_timestamp=NOW,
    )


def technical(direction: TechnicalDirection) -> TechnicalAssessment:
    bullish = 80.0 if direction == TechnicalDirection.BULLISH else 20.0
    bearish = 80.0 if direction == TechnicalDirection.BEARISH else 20.0
    return TechnicalAssessment(
        direction,
        max(bullish, bearish),
        bullish,
        bearish,
        90.0,
        tuple(),
        tuple(),
    )


def test_oi_and_technical_approve_the_existing_oi_side():
    combined = combine_decision(base(), technical(TechnicalDirection.BULLISH))
    assert combined.action == DecisionAction.BUY_CE
    assert combined.approved
    assert not combined.blockers
    assert combined.agreement == "Technical + OI aligned"


def test_technical_conflict_vetoes_a_buy_without_flipping_it():
    raw = base()
    combined = combine_decision(raw, technical(TechnicalDirection.BEARISH))
    gated = apply_confluence_gate(raw, combined)
    assert combined.action == DecisionAction.NO_TRADE
    assert gated.action == DecisionAction.NO_TRADE
    assert any("conflicts" in blocker for blocker in gated.blockers)


def test_unavailable_technical_pillar_blocks_a_new_entry():
    unavailable = TechnicalAssessment(
        TechnicalDirection.UNAVAILABLE,
        0.0,
        0.0,
        0.0,
        0.0,
        tuple(),
        tuple(),
        blockers=("Technical feed missing",),
    )
    combined = combine_decision(base(), unavailable)
    assert combined.action == DecisionAction.NO_TRADE
    assert "Technical feed missing" in combined.blockers


def test_neutral_technical_pillar_blocks_directional_entry():
    neutral = TechnicalAssessment(
        TechnicalDirection.NEUTRAL,
        50.0,
        35.0,
        35.0,
        90.0,
        tuple(),
        tuple(),
    )
    combined = combine_decision(base(), neutral)
    assert combined.action == DecisionAction.NO_TRADE
    assert any("sideways / neutral" in blocker for blocker in combined.blockers)


def test_confluence_never_invents_a_trade_from_oi_no_trade():
    combined = combine_decision(
        base(DecisionAction.NO_TRADE),
        technical(TechnicalDirection.BULLISH),
    )
    assert combined.action == DecisionAction.NO_TRADE
    assert not combined.approved


def test_final_unified_score_must_reach_buy_threshold():
    weak_technical = TechnicalAssessment(
        TechnicalDirection.BULLISH,
        55.0,
        55.0,
        20.0,
        90.0,
        tuple(),
        tuple(),
    )
    raw = replace(base(), score=75.0)

    combined = combine_decision(raw, weak_technical)

    assert combined.action == DecisionAction.NO_TRADE
    assert any("Unified setup score" in blocker for blocker in combined.blockers)


def test_prepare_state_remains_visible_below_final_buy_threshold():
    developing = replace(base(DecisionAction.PREPARE_CE), score=68.0)

    combined = combine_decision(
        developing,
        technical(TechnicalDirection.BULLISH),
    )

    assert combined.action == DecisionAction.PREPARE_CE
    assert not combined.approved
