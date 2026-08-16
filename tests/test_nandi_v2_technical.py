from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from nandi_v2.technical import (
    NANDI_TOP_10_INDICATORS,
    TechnicalDirection,
    assess_technicals,
    indicator_votes,
)


def candles(direction: int, count: int = 80, *, volume: float = 1000.0):
    start = datetime(2026, 8, 10, 9, 15)
    output = []
    price = 25000.0
    for index in range(count):
        opened = price
        closed = price + direction * (2.0 + index * 0.05)
        output.append(
            IntradayCandle(
                timestamp=start + timedelta(minutes=15 * index),
                open=opened,
                high=max(opened, closed) + 3.0,
                low=min(opened, closed) - 3.0,
                close=closed,
                volume=volume + index if volume else 0.0,
            )
        )
        price = closed
    return tuple(output)


def test_technical_lab_exposes_exactly_twenty_five_votes():
    votes = indicator_votes(candles(1))
    assert len(votes) == 25
    assert len({vote.name for vote in votes}) == 25


def test_nandi_top_ten_is_unique_and_present_in_full_catalogue():
    vote_names = {vote.name for vote in indicator_votes(candles(1))}

    assert len(NANDI_TOP_10_INDICATORS) == 10
    assert len(set(NANDI_TOP_10_INDICATORS)) == 10
    assert set(NANDI_TOP_10_INDICATORS) <= vote_names


def test_rising_market_produces_bullish_family_consensus():
    assessment = assess_technicals(candles(1))
    assert assessment.direction == TechnicalDirection.BULLISH
    assert assessment.bullish_score > assessment.bearish_score
    assert assessment.coverage == 100.0


def test_falling_market_produces_bearish_family_consensus():
    assessment = assess_technicals(candles(-1))
    assert assessment.direction == TechnicalDirection.BEARISH
    assert assessment.bearish_score > assessment.bullish_score


def test_missing_index_volume_abstains_instead_of_inventing_votes():
    assessment = assess_technicals(candles(1, volume=0.0))
    volume_votes = [vote for vote in assessment.votes if vote.family == "Participation"]
    assert all(vote.direction == TechnicalDirection.UNAVAILABLE for vote in volume_votes)
    assert assessment.coverage < 100.0


def test_session_vwap_uses_latest_trading_day_only():
    previous = IntradayCandle(
        timestamp=datetime(2026, 8, 10, 15, 15),
        open=100,
        high=102,
        low=98,
        close=100,
        volume=1000,
    )
    latest = IntradayCandle(
        timestamp=datetime(2026, 8, 11, 9, 15),
        open=200,
        high=202,
        low=198,
        close=200,
        volume=1000,
    )

    vwap = next(
        vote for vote in indicator_votes((previous, latest))
        if vote.name == "Session VWAP"
    )

    assert vwap.direction == TechnicalDirection.NEUTRAL
    assert vwap.value == "0.00"


def test_short_history_blocks_directional_technical_approval():
    assessment = assess_technicals(candles(1, count=3))
    assert assessment.direction == TechnicalDirection.UNAVAILABLE
    assert assessment.blockers
