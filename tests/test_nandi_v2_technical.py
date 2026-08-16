from datetime import datetime, timedelta

from nandi_oi.models import IntradayCandle
from nandi_v2.technical import TechnicalDirection, assess_technicals, indicator_votes


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


def test_short_history_blocks_directional_technical_approval():
    assessment = assess_technicals(candles(1, count=3))
    assert assessment.direction == TechnicalDirection.UNAVAILABLE
    assert assessment.blockers
