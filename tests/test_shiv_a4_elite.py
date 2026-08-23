from types import SimpleNamespace

from shiv_v1.engine import SimilarityStats
from shiv_v2.elite import assess_a_plus_plus_plus_plus, wilson_lower_bound
from shiv_v2.replay import WalkForwardResult
from shiv_v2.strategy import SessionBucket, VolatilityBand


def decision(*, actionable=True, quality=96.0, mtf=96.0, persistence=4):
    selected = SimpleNamespace(
        score=95.0,
        spread_pct=1.0,
        volume_ratio=1.4,
        responsiveness_pct=2.5,
    )
    return SimpleNamespace(
        actionable=actionable,
        setup_quality=quality,
        required_quality=78.0,
        required_mtf=78.0,
        base=SimpleNamespace(mtf_agreement=mtf, persistence_count=persistence),
        policy=SimpleNamespace(minimum_persistence=2),
        strike_selection=SimpleNamespace(selected=selected),
        breakout=SimpleNamespace(blocked=False),
        decay=SimpleNamespace(blocked=False),
        session=SimpleNamespace(bucket=SessionBucket.MORNING),
        volatility=SimpleNamespace(band=VolatilityBand.NORMAL),
        blockers=tuple(),
        side="CE",
    )


def walk(*, trades=30, rate=86.0, average=2.5):
    return WalkForwardResult(
        status="WALK-FORWARD OBSERVED",
        sample_size=80,
        folds=tuple(),
        selected_trades=trades,
        wins=26,
        observed_win_rate=rate,
        average_points=average,
        net_points=75.0,
        maximum_losing_streak=2,
        reason="test",
    )


def test_wilson_lower_bound_is_conservative():
    bound = wilson_lower_bound(45, 50)
    assert bound is not None
    assert 75.0 < bound < 90.0


def test_a4_live_candidate_remains_paper_only_without_history():
    result = assess_a_plus_plus_plus_plus(
        decision(),
        SimilarityStats(sample_size=0, wins=0, losses=0, win_rate=None, average_points=None, status="UNVALIDATED"),
        walk(trades=0, rate=None, average=None),
    )
    assert result.live_candidate
    assert not result.validated
    assert "PAPER ONLY" in result.status


def test_a4_validates_only_after_strong_completed_evidence():
    result = assess_a_plus_plus_plus_plus(
        decision(),
        SimilarityStats(sample_size=60, wins=54, losses=6, win_rate=90.0, average_points=3.2, status="VALIDATED SAMPLE (60)"),
        walk(),
    )
    assert result.validated
    assert result.status == "A++++ VALIDATED CE"


def test_a4_locks_when_live_gate_is_not_actionable():
    result = assess_a_plus_plus_plus_plus(
        decision(actionable=False),
        SimilarityStats(sample_size=60, wins=54, losses=6, win_rate=90.0, average_points=3.2, status="VALIDATED SAMPLE (60)"),
        walk(),
    )
    assert not result.live_candidate
    assert result.status == "A++++ LOCKED"
