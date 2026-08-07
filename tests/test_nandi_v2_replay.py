from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nandi_v2.models import MarketContext, OptionChainSnapshot, OptionLeg, StrikeRow
from nandi_v2.replay import NandiReplay


def _frame(now: datetime, spot: float, bullish: bool) -> tuple[OptionChainSnapshot, MarketContext]:
    rows = []
    for strike in range(24350, 24901, 50):
        if bullish:
            ce = OptionLeg(60.0, 10.0, 240000.0, -90000.0, 160000.0, 12.0)
            pe = OptionLeg(35.0, -6.0, 300000.0, 120000.0, 130000.0, 12.0)
        else:
            ce = OptionLeg(35.0, -6.0, 320000.0, 120000.0, 150000.0, 12.0)
            pe = OptionLeg(62.0, 11.0, 250000.0, -90000.0, 180000.0, 12.0)
        rows.append(StrikeRow(float(strike), ce, pe))
    snapshot = OptionChainSnapshot(now, "13-Aug-2026", spot, tuple(rows))
    if bullish:
        context = MarketContext(now, spot - 12.0, spot - 5.0, spot - 50.0, 64.0)
    else:
        context = MarketContext(now, spot + 12.0, spot + 50.0, spot + 5.0, 36.0)
    return snapshot, context


def test_replay_requires_equal_lengths() -> None:
    replay = NandiReplay()
    now = datetime.now(timezone.utc)
    snapshot, context = _frame(now, 24660.0, True)
    try:
        replay.run([snapshot], [])
    except ValueError as exc:
        assert "same length" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_replay_requires_strict_timestamp_order() -> None:
    replay = NandiReplay()
    now = datetime.now(timezone.utc)
    first, first_context = _frame(now, 24660.0, True)
    second, second_context = _frame(now, 24670.0, True)
    try:
        replay.run([first, second], [first_context, second_context])
    except ValueError as exc:
        assert "strictly increasing" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_replay_returns_frames_and_summary_counts() -> None:
    replay = NandiReplay(trade_threshold=75.0, prepare_threshold=65.0)
    now = datetime.now(timezone.utc)
    pairs = [
        _frame(now, 24660.0, True),
        _frame(now + timedelta(minutes=1), 24680.0, True),
        _frame(now + timedelta(minutes=2), 24710.0, True),
    ]
    result = replay.run([p[0] for p in pairs], [p[1] for p in pairs])
    assert len(result.frames) == 3
    assert result.entries >= 0
    assert result.exits >= 0
    assert result.no_trade_frames >= 0
