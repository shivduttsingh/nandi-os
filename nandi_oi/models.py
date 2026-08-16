from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


Side = Literal["CE", "PE"]
Action = Literal["BUY CE", "BUY PE", "NO TRADE"]


@dataclass(frozen=True)
class IntradayCandle:
    """One read-only Upstox OHLC candle for an underlying or option contract."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    open_interest: float = 0.0


@dataclass(frozen=True)
class ATMOptionInstruments:
    """The live nearest-expiry ATM CE and PE instrument identifiers."""

    strike: float
    expiry: str
    ce_instrument_key: str
    pe_instrument_key: str


@dataclass(frozen=True)
class OptionLeg:
    strike: float
    side: Side
    oi: float
    change_oi: float
    ltp: float
    change_ltp: float
    volume: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2
        return ((self.ask - self.bid) / mid * 100) if mid > 0 and self.ask >= self.bid else 0.0

    @property
    def activity(self) -> str:
        if self.change_oi > 0 and self.change_ltp > 0:
            return "FRESH BUYING"
        if self.change_oi > 0 and self.change_ltp < 0:
            return "FRESH WRITING"
        if self.change_oi < 0 and self.change_ltp > 0:
            return "SHORT COVERING"
        if self.change_oi < 0 and self.change_ltp < 0:
            return "LONG UNWINDING"
        return "NEUTRAL"


@dataclass(frozen=True)
class OptionSnapshot:
    timestamp: datetime
    spot: float
    spot_change: float
    recent_high: float
    recent_low: float
    legs: tuple[OptionLeg, ...]
    expiry: str = ""


@dataclass(frozen=True)
class Decision:
    action: Action
    bullish_score: float
    bearish_score: float
    confidence: float
    approved: bool
    selected_strike: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
