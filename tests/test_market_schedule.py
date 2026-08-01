from datetime import date, datetime
from zoneinfo import ZoneInfo

from nandi_oi.market_schedule import IST, MarketSchedule


def test_schedule_uses_ist_instead_of_server_timezone():
    schedule = MarketSchedule()
    utc = ZoneInfo("UTC")
    status = schedule.status(datetime(2026, 8, 3, 4, 0, tzinfo=utc))

    assert status.state == "MARKET_OPEN"
    assert status.observed_at.tzinfo == IST


def test_weekend_waits_for_next_weekday_open():
    schedule = MarketSchedule()
    status = schedule.status(datetime(2026, 8, 1, 14, 6, tzinfo=IST))

    assert status.state == "WEEKEND"
    assert status.next_open == datetime(2026, 8, 3, 9, 15, tzinfo=IST)


def test_closing_time_is_not_a_live_capture_window():
    schedule = MarketSchedule()

    assert schedule.status(datetime(2026, 8, 3, 9, 14, 59, tzinfo=IST)).state == "PRE_MARKET"
    assert schedule.status(datetime(2026, 8, 3, 9, 15, tzinfo=IST)).state == "MARKET_OPEN"
    assert schedule.status(datetime(2026, 8, 3, 15, 30, tzinfo=IST)).state == "MARKET_CLOSED"


def test_configured_holiday_waits_for_the_next_session():
    schedule = MarketSchedule([date(2026, 8, 3)])
    status = schedule.status(datetime(2026, 8, 3, 11, 0, tzinfo=IST))

    assert status.state == "NSE_HOLIDAY"
    assert status.next_open == datetime(2026, 8, 4, 9, 15, tzinfo=IST)
