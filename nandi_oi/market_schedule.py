from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketStatus:
    """The displayed NSE derivatives session state in India Standard Time."""

    state: str
    observed_at: datetime
    next_open: datetime
    reason: str

    @property
    def is_open(self) -> bool:
        return self.state == "MARKET_OPEN"

    @property
    def label(self) -> str:
        return {
            "MARKET_OPEN": "Market open",
            "PRE_MARKET": "Pre-market",
            "MARKET_CLOSED": "Market closed",
            "WEEKEND": "Weekend",
            "NSE_HOLIDAY": "NSE holiday",
        }[self.state]


class MarketSchedule:
    """NSE equity-derivatives session rules without relying on server local time."""

    def __init__(
        self,
        holidays: Iterable[date] = (),
        *,
        open_time: clock_time = clock_time(9, 15),
        close_time: clock_time = clock_time(15, 30),
    ) -> None:
        if open_time >= close_time:
            raise ValueError("Market open time must be before market close time")
        self.holidays = frozenset(holidays)
        self.open_time = open_time
        self.close_time = close_time

    @classmethod
    def from_iso_dates(cls, holiday_dates: Iterable[str]) -> "MarketSchedule":
        values = []
        for value in holiday_dates:
            text = str(value).strip()
            if text:
                values.append(date.fromisoformat(text))
        return cls(values)

    @staticmethod
    def to_ist(now: datetime) -> datetime:
        return now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)

    def _is_trading_day(self, trading_day: date) -> bool:
        return trading_day.weekday() < 5 and trading_day not in self.holidays

    def _next_trading_day(self, start: date) -> date:
        candidate = start
        while not self._is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def _next_open(self, start: date) -> datetime:
        session_day = self._next_trading_day(start)
        return datetime.combine(session_day, self.open_time, tzinfo=IST)

    def status(self, now: datetime | None = None) -> MarketStatus:
        local = self.to_ist(now or datetime.now(IST))
        trading_day = local.date()
        if trading_day.weekday() >= 5:
            return MarketStatus(
                "WEEKEND", local, self._next_open(trading_day + timedelta(days=1)),
                "NSE equity derivatives are closed on weekends.",
            )
        if trading_day in self.holidays:
            return MarketStatus(
                "NSE_HOLIDAY", local, self._next_open(trading_day + timedelta(days=1)),
                "This date is in Nandi's configured NSE holiday calendar.",
            )
        if local.time() < self.open_time:
            return MarketStatus(
                "PRE_MARKET", local, datetime.combine(trading_day, self.open_time, tzinfo=IST),
                "NSE equity derivatives open at 09:15 IST.",
            )
        if local.time() >= self.close_time:
            return MarketStatus(
                "MARKET_CLOSED", local, self._next_open(trading_day + timedelta(days=1)),
                "NSE equity derivatives close at 15:30 IST.",
            )
        return MarketStatus(
            "MARKET_OPEN", local, datetime.combine(trading_day, self.close_time, tzinfo=IST),
            "NSE equity derivatives are in the regular 09:15–15:30 IST session.",
        )
