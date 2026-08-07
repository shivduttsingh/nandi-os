from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nandi_v2.session_gate import build_market_schedule, gate_live_signals

IST = ZoneInfo("Asia/Kolkata")


def test_signal_gate_allows_regular_session() -> None:
    schedule = build_market_schedule()
    result = gate_live_signals(datetime(2026, 8, 7, 10, 30, tzinfo=IST), schedule)
    assert result.allowed
    assert result.status.is_open


def test_signal_gate_blocks_before_open() -> None:
    schedule = build_market_schedule()
    result = gate_live_signals(datetime(2026, 8, 7, 8, 30, tzinfo=IST), schedule)
    assert not result.allowed
    assert "blocked" in result.reason.lower()


def test_signal_gate_blocks_configured_holiday() -> None:
    schedule = build_market_schedule(["2026-08-07"])
    result = gate_live_signals(datetime(2026, 8, 7, 10, 30, tzinfo=IST), schedule)
    assert not result.allowed
    assert result.status.state == "NSE_HOLIDAY"
