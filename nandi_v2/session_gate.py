from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from nandi_oi.market_schedule import MarketSchedule, MarketStatus


@dataclass(frozen=True)
class SessionGateResult:
    allowed: bool
    status: MarketStatus
    reason: str


def build_market_schedule(holiday_dates: Iterable[str] = ()) -> MarketSchedule:
    return MarketSchedule.from_iso_dates(holiday_dates)


def gate_live_signals(now: datetime, schedule: MarketSchedule) -> SessionGateResult:
    status = schedule.status(now)
    if status.is_open:
        return SessionGateResult(True, status, "NSE regular derivatives session is open.")
    return SessionGateResult(False, status, f"Live trade signals blocked: {status.reason}")
