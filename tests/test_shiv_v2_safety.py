from dataclasses import replace
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
from shiv_v2.safety import build_safe_v2_decision


def candle(index, close):
    return IntradayCandle(
        timestamp=datetime(2026, 8, 24, 9, 15) + timedelta(minutes=index * 5),
        open=close - 0.2,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1000 + index * 100,
        open_interest=5000 + index * 100,
    )


def fixture_data():
    nifty = tuple(candle(i, 25000 + i * 5) for i in range(16))
    rows = tuple(
        StrikeRow(
            strike=float(strike),
            ce=OptionLeg(ltp=100, oi=10000, volume=5000, iv=14, bid=99.5, ask=100.5),
            pe=OptionLeg(ltp=100, oi=10000, volume=5000, iv=14, bid=99.5, ask=100.5),
        )
        for strike in (24900, 24950, 25000, 25050, 25100)
    )
    snapshot = OptionChainSnapshot(datetime(2026, 8, 24, 10, 0), "2026-08-27", 25000, rows)
    window = tuple(
        OptionStrikeCandles(
            strike=float(strike), expiry="2026-08-27", offset=offset,
            ce_candles=tuple(candle(i, 90 + i * 2 + offset) for i in range(6)),
            pe_candles=tuple(candle(i, 90 - i + offset) for i in range(6)),
        )
        for offset, strike in zip(range(-2, 3), (24900, 24950, 25000, 25050, 25100))
    )
    return nifty, snapshot, window


def base(stage):
    return ShivDecision(
        stage=stage,
        side="CE",
        setup_quality=85,
        regime=MarketRegime.TREND_UP,
        mtf_direction=Direction.BULLISH,
        mtf_agreement=88,
        persistence_count=3,
        component_scores=tuple(),
        reasons=tuple(),
        blockers=("V1 hard blocker",) if stage == SetupStage.NO_TRADE else tuple(),
        entry_plan=EntryPlan("ENTRY READY", 25000, 100, 96.5, 108, 110, 3.5, "test"),
        similarity=SimilarityStats(),
        signature="tf=5|regime=TRENDING UP|mtf=BULLISH|atm=CONFIRM CE|window=STRONG CE|oi=CE",
    )


def mtf(conflict=False):
    return MultiTimeframeAssessment(Direction.BULLISH, 88, 0.8, 0.1, 0.1, conflict, tuple(), "test")


def test_v2_cannot_override_v1_hard_no_trade():
    nifty, snapshot, window = fixture_data()
    decision = build_safe_v2_decision(
        base=base(SetupStage.NO_TRADE),
        primary_regime=RegimeAssessment(MarketRegime.TREND_UP, Direction.BULLISH, .8, .4, .3, "test"),
        mtf=mtf(),
        snapshot=snapshot,
        option_window=window,
        primary_nifty=nifty,
        now=datetime(2026, 8, 24, 10, 0),
        first_seen_at=datetime(2026, 8, 24, 9, 55),
        first_premium=98,
        expiry="2026-08-27",
    )
    assert decision.status == "NO TRADE"
    assert decision.entry_plan.status == "WAIT — HARD GATE"
    assert "V1 hard blocker" in decision.blockers


def test_v2_cannot_override_mtf_conflict():
    nifty, snapshot, window = fixture_data()
    decision = build_safe_v2_decision(
        base=base(SetupStage.STRONG_CE),
        primary_regime=RegimeAssessment(MarketRegime.TREND_UP, Direction.BULLISH, .8, .4, .3, "test"),
        mtf=mtf(conflict=True),
        snapshot=snapshot,
        option_window=window,
        primary_nifty=nifty,
        now=datetime(2026, 8, 24, 10, 0),
        first_seen_at=datetime(2026, 8, 24, 9, 55),
        first_premium=98,
        expiry="2026-08-27",
    )
    assert decision.status == "NO TRADE"
    assert any("conflict" in blocker.lower() for blocker in decision.blockers)
