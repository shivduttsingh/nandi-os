from datetime import datetime, timedelta, timezone

from nandi_v2.results import completed_trades, result_rows


NOW = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)


def event(minutes, status, side, spot, reason="", entry_premium=None, current_premium=None):
    value = {
        "Time": (NOW + timedelta(minutes=minutes)).isoformat(),
        "Status": status,
        "Side": side,
        "Spot": spot,
        "Score": 82.0,
        "Strike": 25000.0,
        "Reason": reason,
    }
    value["Entry premium"] = entry_premium
    value["Current premium"] = current_premium
    return value


def test_completed_trades_pair_entries_and_directional_points():
    events = [
        event(0, "ACTIVE CE", "CE", 25000),
        event(5, "HOLD", "CE", 25010),
        event(15, "EXIT", "CE", 25030, "Target"),
        event(30, "ACTIVE PE", "PE", 25040),
        event(50, "EXIT", "PE", 25000, "Target"),
    ]

    trades = completed_trades(events)

    assert [trade.points for trade in trades] == [30.0, 40.0]
    assert [trade.hold_minutes for trade in trades] == [15.0, 20.0]


def test_daily_weekly_monthly_results_include_win_rate_and_drawdown():
    trades = completed_trades([
        event(0, "ACTIVE CE", "CE", 25000),
        event(15, "EXIT", "CE", 25030),
        event(30, "ACTIVE CE", "CE", 25030),
        event(45, "EXIT", "CE", 25010),
    ])

    daily = result_rows(trades, "daily")
    weekly = result_rows(trades, "weekly")
    monthly = result_rows(trades, "monthly")

    assert daily[0]["Win rate %"] == 50.0
    assert daily[0]["Net NIFTY points"] == 10.0
    assert daily[0]["Maximum drawdown"] == 20.0
    assert len(weekly) == len(monthly) == 1


def test_recorded_option_premiums_drive_separate_premium_results():
    trades = completed_trades([
        event(0, "ACTIVE CE", "CE", 25000, entry_premium=100.0, current_premium=100.0),
        event(15, "EXIT", "CE", 25020, current_premium=112.5),
    ])

    assert trades[0].premium_points == 12.5
    assert trades[0].premium_return_pct == 12.5
    daily = result_rows(trades, "daily")[0]
    assert daily["Premium win rate %"] == 100.0
    assert daily["Net premium points"] == 12.5
