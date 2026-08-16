from datetime import datetime, timedelta

from nandi_v2.fundamentals import (
    FUNDAMENTAL_CATALOGUE,
    FundamentalBias,
    FundamentalFactor,
    assess_fundamentals,
)
from nandi_v2.history import DecisionHistory


NOW = datetime(2026, 8, 10, 10, 0)


def factors(bias: FundamentalBias, observed_at: datetime = NOW):
    return tuple(
        FundamentalFactor(
            key=definition.key,
            name=definition.name,
            category=definition.category,
            bias=bias,
            impact=80.0,
            confidence=0.9,
            observed_at=observed_at,
            max_age_minutes=definition.max_age_minutes,
            source="Test source",
            note="Auditable test evidence",
        )
        for definition in FUNDAMENTAL_CATALOGUE
    )


def test_fresh_bullish_fundamentals_are_directional():
    assessment = assess_fundamentals(factors(FundamentalBias.BULLISH), NOW)
    assert assessment.direction == FundamentalBias.BULLISH
    assert assessment.coverage == 100.0
    assert assessment.bullish_score == 100.0


def test_stale_fundamentals_block_the_pillar():
    stale = NOW - timedelta(days=10)
    assessment = assess_fundamentals(factors(FundamentalBias.BEARISH, stale), NOW)
    assert assessment.direction == FundamentalBias.UNKNOWN
    assert assessment.coverage == 0.0
    assert assessment.blockers


def test_neutral_fresh_inputs_are_known_but_non_directional():
    assessment = assess_fundamentals(factors(FundamentalBias.NEUTRAL), NOW)
    assert assessment.direction == FundamentalBias.NEUTRAL
    assert assessment.coverage == 100.0


def test_fundamental_snapshot_round_trips_through_sqlite(tmp_path):
    store = DecisionHistory(str(tmp_path / "nandi.sqlite"))
    expected = factors(FundamentalBias.BULLISH)
    assert store.append_fundamental_factors(expected, recorded_at=NOW) == len(FUNDAMENTAL_CATALOGUE)
    restored = store.latest_fundamental_factors()
    assert len(restored) == len(expected)
    assert restored[0].source == "Test source"
    assert all(factor.bias == FundamentalBias.BULLISH for factor in restored)
