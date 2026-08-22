from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from nandi_v2.atm_strategy import ATMConfirmationAssessment, ATMConfirmationSignal
from nandi_v2.strike_window_strategy import StrikeWindowAssessment, StrikeWindowSignal
from shiv_v1.engine import (
    Direction,
    MarketRegime,
    SetupStage,
    SimilarityStats,
    assess_timeframe,
    build_entry_plan,
    build_shiv_decision,
    classify_market_regime,
    combine_timeframes,
    infer_candidate_side,
    manage_paper_exit,
    next_persistence,
)


def candle(minute: int, close: float, *, high: float | None = None, low: float | None = None, opened: float | None = None) -> IntradayCandle:
    opened = close - 1.0 if opened is None else opened
    return IntradayCandle(
        timestamp=datetime(2026, 8, 22, 9, 15) + timedelta(minutes=minute),
        open=opened,
        high=high if high is not None else max(opened, close) + 0.5,
        low=low if low is not None else min(opened, close) - 0.5,
        close=close,
        volume=1000 + minute,
        open_interest=5000 + minute,
    )


def bullish_series() -> tuple[IntradayCandle, ...]:
    values = [100, 101, 102, 103, 104, 105, 106, 108, 110, 113, 116, 120]
    return tuple(candle(index * 5, value) for index, value in enumerate(values))


def bearish_series() -> tuple[IntradayCandle, ...]:
    values = [120, 119, 118, 117, 116, 114, 112, 110, 107, 104, 101, 97]
    return tuple(candle(index * 5, value, opened=value + 1.0) for index, value in enumerate(values))


def sideways_series() -> tuple[IntradayCandle, ...]:
    values = [100, 101, 100.2, 100.8, 100.1, 100.7, 100.2, 100.6, 100.3, 100.5, 100.4, 100.45]
    return tuple(candle(index * 5, value, opened=value) for index, value in enumerate(values))


def atm(side: str) -> ATMConfirmationAssessment:
    return ATMConfirmationAssessment(
        ATMConfirmationSignal.CONFIRM_CE if side == "CE" else ATMConfirmationSignal.CONFIRM_PE,
        90.0,
        0.2 if side == "CE" else -0.2,
        4.0 if side == "CE" else -2.0,
        4.0 if side == "PE" else -2.0,
        "matching ATM evidence",
    )


def strike(side: str, score: float = 90.0) -> StrikeWindowAssessment:
    return StrikeWindowAssessment(
        signal=StrikeWindowSignal.CONFIRM_CE if side == "CE" else StrikeWindowSignal.CONFIRM_PE,
        agreement_score=score,
        nifty_change_pct=0.2 if side == "CE" else -0.2,
        ce_median_change_pct=3.0 if side == "CE" else -1.0,
        pe_median_change_pct=3.0 if side == "PE" else -1.0,
        ce_positive_strikes=5 if side == "CE" else 0,
        pe_positive_strikes=5 if side == "PE" else 0,
        dominant_strikes=5,
        reason="matching window",
        status_label=f"STRONG {side}",
        weighted_dominance_pct=90.0,
        nifty_structure="BULLISH" if side == "CE" else "BEARISH",
        oi_confirmation="SUPPORTS",
        volume_confirmation="EXPANDING",
        vwap_confirmation="BULLISH" if side == "CE" else "BEARISH",
        trend_efficiency=0.8,
        persistence_bars=2,
    )


def test_regime_detects_clean_uptrend_and_downtrend():
    assert classify_market_regime(bullish_series()).direction == Direction.BULLISH
    assert classify_market_regime(bearish_series()).direction == Direction.BEARISH


def test_regime_rejects_chop_as_sideways():
    result = classify_market_regime(sideways_series())
    assert result.regime == MarketRegime.SIDEWAYS
    assert result.direction == Direction.NEUTRAL


def test_multi_timeframe_conflict_is_visible():
    rows = (
        assess_timeframe(1, bullish_series()),
        assess_timeframe(3, bearish_series()),
        assess_timeframe(5, bullish_series()),
        assess_timeframe(15, bearish_series()),
    )
    result = combine_timeframes(rows)
    assert result.conflict


def test_candidate_requires_directional_edge():
    bullish = combine_timeframes(
        (
            assess_timeframe(1, bullish_series()),
            assess_timeframe(3, bullish_series()),
            assess_timeframe(5, bullish_series()),
            assess_timeframe(15, bullish_series()),
        )
    )
    assert infer_candidate_side(bullish, atm("CE"), strike("CE"), "CE") == "CE"
    assert infer_candidate_side(bullish, atm("PE"), strike("PE"), "PE") == "PE"


def test_persistence_resets_on_side_flip():
    side, count = next_persistence("CE", 2, "CE")
    assert side == "CE" and count == 3
    side, count = next_persistence(side, count, "PE")
    assert side == "PE" and count == 1


def test_entry_waits_for_trigger_instead_of_chasing():
    values = (
        candle(0, 100, opened=99),
        candle(5, 101, opened=100),
        candle(10, 100.5, opened=101),
    )
    plan = build_entry_plan("CE", 25000, values, setup_quality=80)
    assert plan.status == "WAIT — ENTRY TRIGGER"


def test_entry_ready_after_completed_option_breakout():
    values = (
        candle(0, 100, high=101, opened=99),
        candle(5, 101, high=102, opened=100),
        candle(10, 104, high=105, low=100.5, opened=101),
    )
    plan = build_entry_plan("CE", 25000, values, setup_quality=80)
    assert plan.status == "ENTRY READY"
    assert plan.stop == 100.5
    assert plan.target_1 == 112.0


def test_exit_ladder_moves_to_breakeven_and_trails():
    breakeven = manage_paper_exit(100, 104.2, 104.5)
    assert breakeven.status == "BREAKEVEN"
    trail = manage_paper_exit(100, 106.5, 107.0)
    assert trail.status == "TRAIL"
    target = manage_paper_exit(100, 110.2, 110.5)
    assert target.status == "EXIT — TARGET 2"


def test_high_quality_aligned_setup_becomes_strong_after_persistence():
    mtf = combine_timeframes(
        (
            assess_timeframe(1, bullish_series()),
            assess_timeframe(3, bullish_series()),
            assess_timeframe(5, bullish_series()),
            assess_timeframe(15, bullish_series()),
        )
    )
    regime = classify_market_regime(bullish_series())
    decision = build_shiv_decision(
        interval_minutes=5,
        primary_regime=regime,
        mtf=mtf,
        atm=atm("CE"),
        strike=strike("CE", 100),
        oi_side="CE",
        oi_score=100,
        candidate_side="CE",
        persistence_count=3,
        option_spread_pct=1.0,
        option_strike=25000,
        option_candles=(
            candle(0, 100, high=101, opened=99),
            candle(5, 101, high=102, opened=100),
            candle(10, 104, high=105, low=100.5, opened=101),
        ),
        similarity=SimilarityStats(),
    )
    assert decision.stage in {SetupStage.STRONG_CE, SetupStage.A_PLUS_CE}
    assert decision.setup_quality >= 75
    assert decision.actionable


def test_sideways_is_a_hard_no_trade_blocker():
    mtf = combine_timeframes(
        (
            assess_timeframe(1, sideways_series()),
            assess_timeframe(3, sideways_series()),
            assess_timeframe(5, sideways_series()),
            assess_timeframe(15, sideways_series()),
        )
    )
    decision = build_shiv_decision(
        interval_minutes=5,
        primary_regime=classify_market_regime(sideways_series()),
        mtf=mtf,
        atm=atm("CE"),
        strike=strike("CE"),
        oi_side="CE",
        oi_score=90,
        candidate_side="CE",
        persistence_count=3,
        option_spread_pct=1.0,
        option_strike=25000,
        option_candles=bullish_series(),
        similarity=SimilarityStats(),
    )
    assert decision.stage == SetupStage.NO_TRADE
    assert any("SIDEWAYS" in blocker for blocker in decision.blockers)
