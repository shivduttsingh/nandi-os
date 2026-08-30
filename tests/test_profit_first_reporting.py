from datetime import date, datetime, timezone

import pandas as pd

from nandi_v2.profit_first_reporting import (
    all_period_summaries,
    forward_run_row,
    forward_trade_rows,
    merge_forward_ledger,
    merge_forward_runs,
    rupee_pnl,
    validation_status,
)


def sample_trades() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "signal_dt": "2026-01-02 12:00:00",
                "entry_dt": "2026-01-02 12:01:00",
                "exit_dt": "2026-01-02 12:30:00",
                "expiry": "2026-01-06",
                "side": "CE",
                "strike": 26000.0,
                "signal_spot": 25990.0,
                "spot_r1_pct": -0.06,
                "entry": 100.0,
                "exit": 110.5,
                "pnl": 10.0,
            },
            {
                "signal_dt": "2026-01-05 12:10:00",
                "entry_dt": "2026-01-05 12:11:00",
                "exit_dt": "2026-01-05 12:40:00",
                "expiry": "2026-01-06",
                "side": "PE",
                "strike": 26100.0,
                "signal_spot": 26110.0,
                "spot_r1_pct": 0.07,
                "entry": 120.0,
                "exit": 115.5,
                "pnl": -5.0,
            },
            {
                "signal_dt": "2026-02-02 13:00:00",
                "entry_dt": "2026-02-02 13:01:00",
                "exit_dt": "2026-02-02 13:30:00",
                "expiry": "2026-02-03",
                "side": "CE",
                "strike": 26500.0,
                "signal_spot": 26490.0,
                "spot_r1_pct": -0.08,
                "entry": 90.0,
                "exit": 105.5,
                "pnl": 15.0,
            },
        ]
    )


def test_period_summaries_include_daily_weekly_and_monthly():
    overall, daily, weekly, monthly = all_period_summaries(sample_trades())
    assert overall["trades"] == 3
    assert overall["wins"] == 2
    assert overall["net_points"] == 20.0
    assert len(daily) == 3
    assert list(weekly["week"]) == ["2026-W01", "2026-W02", "2026-W06"]
    assert list(monthly["month"]) == ["2026-01", "2026-02"]
    assert float(monthly.iloc[0]["net_points"]) == 5.0


def test_forward_ledger_merge_is_idempotent():
    recorded = datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc)
    incoming = forward_trade_rows(sample_trades().iloc[:1], test_date=date(2026, 8, 31), recorded_at=recorded)
    merged = merge_forward_ledger(pd.DataFrame(), incoming)
    merged_again = merge_forward_ledger(merged, incoming)
    assert len(merged_again) == 1
    assert merged_again.iloc[0]["source"] == "UPSTOX_FORWARD_CLOSE"


def test_run_log_replaces_same_test_date():
    first = forward_run_row(
        {"trades": 1, "wins": 1, "losses": 0, "win_rate": 100.0, "net_points": 5.0, "max_drawdown": 0.0},
        test_date=date(2026, 8, 31),
    )
    second = forward_run_row(
        {"trades": 2, "wins": 1, "losses": 1, "win_rate": 50.0, "net_points": 2.0, "max_drawdown": 3.0},
        test_date=date(2026, 8, 31),
    )
    merged = merge_forward_runs(first, second)
    assert len(merged) == 1
    assert int(merged.iloc[0]["trades"]) == 2


def test_validation_status_collecting_pass_and_fail():
    status, _ = validation_status({"trades": 99, "net_points": 100.0})
    assert status == "COLLECTING"

    status, reasons = validation_status(
        {"trades": 100, "win_rate": 72.0, "profit_factor": 1.8, "net_points": 50.0}
    )
    assert status == "PASS"
    assert reasons == []

    status, reasons = validation_status(
        {"trades": 100, "win_rate": 55.0, "profit_factor": 1.2, "net_points": -5.0}
    )
    assert status == "FAIL"
    assert len(reasons) == 3


def test_rupee_pnl_is_points_times_quantity():
    assert rupee_pnl(10.5, 130) == 1365.0
