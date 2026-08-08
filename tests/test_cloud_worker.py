from datetime import datetime
from zoneinfo import ZoneInfo

from nandi_v2.cloud_worker import WorkerConfig, is_weekday, worker_window

IST = ZoneInfo("Asia/Kolkata")


def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def test_weekday_worker_window() -> None:
    config = WorkerConfig()
    # Monday
    assert worker_window(at(2026, 8, 10, 8, 54), config) == "OFF"
    assert worker_window(at(2026, 8, 10, 8, 55), config) == "WARMUP"
    assert worker_window(at(2026, 8, 10, 9, 14), config) == "WARMUP"
    assert worker_window(at(2026, 8, 10, 9, 15), config) == "LIVE"
    assert worker_window(at(2026, 8, 10, 15, 30), config) == "LIVE"
    assert worker_window(at(2026, 8, 10, 15, 31), config) == "COOLDOWN"
    assert worker_window(at(2026, 8, 10, 15, 59), config) == "COOLDOWN"
    assert worker_window(at(2026, 8, 10, 16, 0), config) == "OFF"


def test_weekend_is_always_off() -> None:
    config = WorkerConfig()
    saturday = at(2026, 8, 8, 10, 0)
    sunday = at(2026, 8, 9, 10, 0)
    assert not is_weekday(saturday)
    assert not is_weekday(sunday)
    assert worker_window(saturday, config) == "OFF"
    assert worker_window(sunday, config) == "OFF"
