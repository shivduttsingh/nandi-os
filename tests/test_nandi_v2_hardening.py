from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nandi_v2.history import DecisionHistory
from nandi_v2.models import (
    Decision,
    DecisionAction,
    OptionChainSnapshot,
    OptionLeg,
    ScoreBreakdown,
    StrikeRow,
    TradeLevels,
)
from nandi_v2.nse import NSEDataError, NSEPublicClient

IST = ZoneInfo("Asia/Kolkata")


def _snapshot(minute: int, ce_oi: float, pe_oi: float, ce_ltp: float, pe_ltp: float, volume: float) -> OptionChainSnapshot:
    stamp = datetime(2026, 8, 6, 12, minute, tzinfo=IST)
    row = StrikeRow(
        24650.0,
        OptionLeg(ce_ltp, 99.0, ce_oi, 999.0, volume, 12.0),
        OptionLeg(pe_ltp, -99.0, pe_oi, -999.0, volume, 13.0),
    )
    return OptionChainSnapshot(stamp, "13-Aug-2026", 24650.0, (row,), "test")


def _decision(action: DecisionAction, stamp: datetime) -> Decision:
    score = ScoreBreakdown(20, 20, 15, 15, 10, 10, 5, 5)
    return Decision(
        action=action,
        score=100,
        ce_score=100 if action == DecisionAction.BUY_CE else 10,
        pe_score=100 if action == DecisionAction.BUY_PE else 10,
        selected_strike=24650,
        market_state="TREND",
        breakdown=score,
        opposite_breakdown=score,
        levels=TradeLevels(entry=24650, stop=24620, target_1=24700, target_2=24750),
        generated_at=stamp,
        data_timestamp=stamp,
    )


def test_rolling_snapshot_uses_previous_oi_ltp_and_volume() -> None:
    previous = _snapshot(0, 1000, 1200, 100, 90, 5000)
    current = _snapshot(1, 1150, 1100, 108, 96, 5600)
    rolling = NSEPublicClient.rolling_snapshot(current, previous)
    row = rolling.rows[0]
    assert row.ce.change_oi == 150
    assert row.pe.change_oi == -100
    assert row.ce.change == 8
    assert row.pe.change == 6
    assert row.ce.volume == 600
    assert "rolling delta" in rolling.source


def test_first_rolling_snapshot_is_neutral_baseline() -> None:
    rolling = NSEPublicClient.rolling_snapshot(_snapshot(0, 1000, 1200, 100, 90, 5000), None)
    row = rolling.rows[0]
    assert row.ce.change_oi == 0
    assert row.pe.change_oi == 0
    assert row.ce.change == 0
    assert row.pe.change == 0
    assert row.ce.volume == 5000
    assert "baseline" in rolling.source


def test_invalid_required_nse_timestamp_is_rejected() -> None:
    with pytest.raises(NSEDataError):
        NSEPublicClient._timestamp("", required=True)


def test_email_signal_key_is_stable_across_minute_and_spot(tmp_path) -> None:
    history = DecisionHistory(str(tmp_path / "history.sqlite"))
    first = _decision(DecisionAction.BUY_CE, datetime(2026, 8, 6, 10, 1, tzinfo=IST))
    second = _decision(DecisionAction.BUY_CE, datetime(2026, 8, 6, 10, 59, tzinfo=IST))
    assert history.signal_key(first, 24650.0, "13-Aug-2026") == history.signal_key(second, 24710.0, "13-Aug-2026")


def test_failed_email_can_be_retried_but_delivered_email_is_deduplicated(tmp_path) -> None:
    history = DecisionHistory(str(tmp_path / "history.sqlite"))
    decision = _decision(DecisionAction.BUY_PE, datetime(2026, 8, 6, 11, 0, tzinfo=IST))
    key = history.signal_key(decision, 24650.0, "13-Aug-2026")
    history.record_alert(key, False, "temporary failure")
    assert not history.alert_exists(key)
    history.record_alert(key, True)
    assert history.alert_exists(key)
