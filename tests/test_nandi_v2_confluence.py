from datetime import datetime, timezone

from nandi_v2.confluence import apply_confluence_gate, combine_decision
from nandi_v2.fundamentals import FundamentalAssessment, FundamentalBias
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
    return TechnicalAssessment(direction, max(bullish, bearish), bullish, bearish, 90.0, tuple(), tuple())


def fundamental(direction: FundamentalBias) -> FundamentalAssessment:
    bullish = 75.0 if direction == FundamentalBias.BULLISH else 15.0
    bearish = 75.0 if direction == FundamentalBias.BEARISH else 15.0
    if direction == FundamentalBias.NEUTRAL:
        bullish = bearish = 20.0
    return FundamentalAssessment(direction, 70.0, bullish, bearish, 100.0, tuple())


def test_all_three_pillars_approve_the_existing_oi_side():
    combined = combine_decision(
        base(),
        technical(TechnicalDirection.BULLISH),
        fundamental(FundamentalBias.BULLISH),
    )
    assert combined.action == DecisionAction.BUY_CE
    assert combined.approved
    assert not combined.blockers


def test_technical_conflict_vetoes_a_buy_without_flipping_it():
    raw = base()
    combined = combine_decision(raw, technical(TechnicalDirection.BEARISH), fundamental(FundamentalBias.BULLISH))
    gated = apply_confluence_gate(raw, combined)
    assert combined.action == DecisionAction.NO_TRADE
    assert gated.action == DecisionAction.NO_TRADE
    assert any("conflicts" in blocker for blocker in gated.blockers)


def test_unknown_fundamental_pillar_blocks_a_new_entry():
    unknown = FundamentalAssessment(
        FundamentalBias.UNKNOWN,
        0.0,
        0.0,
        0.0,
        0.0,
        tuple(),
        blockers=("Fundamental feed missing",),
    )
    combined = combine_decision(base(), technical(TechnicalDirection.BULLISH), unknown)
    assert combined.action == DecisionAction.NO_TRADE
    assert "Fundamental feed missing" in combined.blockers


def test_fresh_neutral_fundamentals_do_not_overrule_technical_and_oi_alignment():
    combined = combine_decision(
        base(),
        technical(TechnicalDirection.BULLISH),
        fundamental(FundamentalBias.NEUTRAL),
    )
    assert combined.action == DecisionAction.BUY_CE
    assert combined.approved


def test_confluence_never_invents_a_trade_from_oi_no_trade():
    combined = combine_decision(
        base(DecisionAction.NO_TRADE),
        technical(TechnicalDirection.BULLISH),
        fundamental(FundamentalBias.BULLISH),
    )
    assert combined.action == DecisionAction.NO_TRADE
    assert not combined.approved
