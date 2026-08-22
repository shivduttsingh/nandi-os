from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.models import OptionChainSnapshot, OptionLeg, StrikeRow
from shiv_v1.engine import (
    Direction,
    EntryPlan,
    MarketRegime,
    MultiTimeframeAssessment,
    RegimeAssessment,
    SetupStage,
    ShivDecision,
    SimilarityStats,
)
from shiv_v2.strategy import (
    SessionBucket,
    VolatilityBand,
    build_v2_decision,
    classify_session,
    detect_mw_pattern,
    false_breakout_check,
    manage_adaptive_exit,
    policy_for,
    select_option_strike,
    setup_decay,
    volatility_context,
)


def candle(index: int, close: float, *, low=None, high=None, volume=1000, oi=5000):
    return IntradayCandle(
        timestamp=datetime(2026, 8, 24, 9, 15) + timedelta(minutes=index * 5),
        open=close - 0.2,
        high=float(high if high is not None else close + 0.5),
        low=float(low if low is not None else close - 0.5),
        close=float(close),
        volume=float(volume),
        open_interest=float(oi),
    )


def series(values):
    return tuple(candle(index, value, volume=1000 + index * 100) for index, value in enumerate(values))


def option_series(values, *, volume_start=1000, oi=5000):
    return tuple(candle(index, value, volume=volume_start + index * 300, oi=oi + index * 100) for index, value in enumerate(values))


def snapshot():
    rows = []
    for strike, ce_spread, pe_spread, ce_ltp, pe_ltp in (
        (24900, 2.0, 2.0, 130, 70),
        (24950, 1.5, 1.5, 110, 85),
        (25000, 3.0, 3.0, 95, 95),
        (25050, 0.8, 0.8, 78, 112),
        (25100, 2.0, 2.0, 62, 135),
    ):
        rows.append(StrikeRow(
            strike=float(strike),
            ce=OptionLeg(ltp=ce_ltp, oi=10000, volume=5000, iv=14, bid=ce_ltp - ce_spread / 2, ask=ce_ltp + ce_spread / 2),
            pe=OptionLeg(ltp=pe_ltp, oi=10000, volume=5000, iv=15, bid=pe_ltp - pe_spread / 2, ask=pe_ltp + pe_spread / 2),
        ))
    return OptionChainSnapshot(
        timestamp=datetime(2026, 8, 24, 10, 0),
        expiry="2026-08-27",
        spot=25000,
        rows=tuple(rows),
        source="TEST",
    )


def option_window():
    values_by_offset = {
        -2: [120, 121, 123, 125],
        -1: [100, 102, 106, 112],
        0: [90, 91, 93, 96],
        1: [72, 73, 74, 75],
        2: [58, 58.5, 59, 59.5],
    }
    strikes = {-2: 24900, -1: 24950, 0: 25000, 1: 25050, 2: 25100}
    output = []
    for offset in range(-2, 3):
        output.append(OptionStrikeCandles(
            strike=float(strikes[offset]),
            expiry="2026-08-27",
            offset=offset,
            ce_candles=option_series(values_by_offset[offset], volume_start=1200 if offset == -1 else 700),
            pe_candles=option_series(list(reversed(values_by_offset[offset])), volume_start=700),
        ))
    return tuple(output)


def base_decision(side="CE", quality=82.0, persistence=3):
    return ShivDecision(
        stage=SetupStage.STRONG_CE if side == "CE" else SetupStage.STRONG_PE,
        side=side,
        setup_quality=quality,
        regime=MarketRegime.TREND_UP if side == "CE" else MarketRegime.TREND_DOWN,
        mtf_direction=Direction.BULLISH if side == "CE" else Direction.BEARISH,
        mtf_agreement=85.0,
        persistence_count=persistence,
        component_scores=tuple(),
        reasons=tuple(),
        blockers=tuple(),
        entry_plan=EntryPlan("ENTRY READY", 25000, 100, 96.5, 108, 110, 3.5, "test"),
        similarity=SimilarityStats(),
        signature=f"tf=5|regime=TRENDING UP|mtf=BULLISH|atm=CONFIRM CE|window=STRONG CE|oi={side}",
    )


def mtf(direction=Direction.BULLISH, agreement=85.0):
    return MultiTimeframeAssessment(
        direction=direction,
        agreement_score=agreement,
        bullish_weight=0.8 if direction == Direction.BULLISH else 0.1,
        bearish_weight=0.8 if direction == Direction.BEARISH else 0.1,
        neutral_weight=0.1,
        conflict=False,
        rows=tuple(),
        reason="test",
    )


def test_regime_policy_is_stricter_for_reversal_and_fast_bars():
    trend = policy_for(MarketRegime.TREND_UP, 5)
    reversal = policy_for(MarketRegime.REVERSAL_UP, 5)
    fast = policy_for(MarketRegime.TREND_UP, 1)
    assert reversal.minimum_quality > trend.minimum_quality
    assert reversal.minimum_persistence > trend.minimum_persistence
    assert fast.minimum_quality > trend.minimum_quality
    assert fast.minimum_persistence >= 3


def test_time_of_day_filter_tightens_midday_and_blocks_final_minutes():
    morning = classify_session(datetime(2026, 8, 24, 10, 0))
    midday = classify_session(datetime(2026, 8, 24, 12, 15))
    final = classify_session(datetime(2026, 8, 24, 15, 25))
    assert morning.bucket == SessionBucket.MORNING
    assert midday.quality_adjustment > morning.quality_adjustment
    assert not final.allow_new_entries


def test_volatility_and_expiry_context_are_explicit():
    calm = series([25000, 25002, 25001, 25003, 25004, 25005, 25004, 25006, 25007, 25008, 25009, 25010, 25011, 25012, 25013, 25014])
    result = volatility_context(calm, atm_iv=13.5, expiry="2026-08-24", now=datetime(2026, 8, 24, 10, 0))
    assert result.days_to_expiry == 0
    assert result.expiry_label == "EXPIRY DAY"
    assert result.band in {VolatilityBand.LOW, VolatilityBand.NORMAL}
    assert result.quality_adjustment >= 5


def test_confirmed_w_and_m_patterns_are_detected():
    w_values = [101, 99, 96, 99, 102, 99, 96.1, 100, 103.5]
    w = detect_mw_pattern(series(w_values))
    assert w.side == "CE"
    assert w.confirmed
    assert w.label == "W CONFIRMED"

    m_values = [99, 101, 104, 101, 98, 101, 103.9, 100, 96.5]
    m = detect_mw_pattern(series(m_values))
    assert m.side == "PE"
    assert m.confirmed
    assert m.label == "M CONFIRMED"


def test_false_breakout_rejects_failed_follow_through():
    nifty = series([100, 101, 103, 101.5])
    option = option_series([50, 52, 55, 53])
    result = false_breakout_check("CE", nifty, option)
    assert result.blocked
    assert "FALSE" in result.status


def test_setup_decay_marks_old_or_chased_setup():
    now = datetime(2026, 8, 24, 10, 30)
    old = setup_decay(
        first_seen_at=now - timedelta(minutes=30),
        now=now,
        interval_minutes=5,
        first_premium=100,
        current_premium=102,
        maximum_chase_pct=6,
    )
    chased = setup_decay(
        first_seen_at=now - timedelta(minutes=5),
        now=now,
        interval_minutes=5,
        first_premium=100,
        current_premium=108,
        maximum_chase_pct=6,
    )
    assert old.blocked and old.status == "EXPIRED"
    assert chased.blocked and "DO NOT CHASE" in chased.status


def test_contract_selector_can_choose_better_near_atm_contract():
    selection = select_option_strike(snapshot(), option_window(), "CE")
    assert selection.selected is not None
    # ATM -1 has deliberately stronger premium response/volume than ATM in this fixture.
    assert selection.selected.offset == -1
    assert selection.selected.score >= 50


def test_adaptive_exit_books_reversal_earlier_than_trend():
    from shiv_v2.strategy import volatility_context
    candles = series([25000 + index * 2 for index in range(16)])
    vol = volatility_context(candles, atm_iv=14, expiry="2026-08-27", now=datetime(2026, 8, 24, 10, 0))
    reversal = manage_adaptive_exit(100, 109, 109.5, regime=MarketRegime.REVERSAL_UP, volatility=vol)
    trend = manage_adaptive_exit(100, 109, 109.5, regime=MarketRegime.TREND_UP, volatility=vol)
    assert reversal.status == "EXIT — TARGET 2"
    assert trend.status != "EXIT — TARGET 2"


def test_v2_build_applies_adaptive_quality_and_contract_selection():
    nifty = series([24980, 24985, 24990, 24996, 25002, 25008, 25014, 25020, 25026, 25032, 25038, 25044, 25050, 25056, 25062, 25068])
    regime = RegimeAssessment(MarketRegime.TREND_UP, Direction.BULLISH, 0.8, 0.4, 0.3, "test")
    decision = build_v2_decision(
        base=base_decision(),
        primary_regime=regime,
        mtf=mtf(),
        snapshot=snapshot(),
        option_window=option_window(),
        primary_nifty=nifty,
        now=datetime(2026, 8, 24, 10, 0),
        first_seen_at=datetime(2026, 8, 24, 9, 55),
        first_premium=108,
        expiry="2026-08-27",
    )
    assert decision.strike_selection.selected is not None
    assert decision.required_quality >= decision.policy.minimum_quality
    assert decision.session.bucket == SessionBucket.MORNING
    assert decision.volatility.band != VolatilityBand.UNAVAILABLE
    assert decision.status in {"CONFIRM CE", "STRONG CE", "A+ CE", "WAIT", "NO TRADE"}
