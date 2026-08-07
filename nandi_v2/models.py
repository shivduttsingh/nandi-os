from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DecisionAction(str, Enum):
    BUY_CE = "BUY CE"
    BUY_PE = "BUY PE"
    PREPARE_CE = "PREPARE CE"
    PREPARE_PE = "PREPARE PE"
    NO_TRADE = "NO TRADE"


@dataclass(frozen=True)
class OptionLeg:
    ltp: float = 0.0
    change: float = 0.0
    oi: float = 0.0
    change_oi: float = 0.0
    volume: float = 0.0
    iv: float = 0.0


@dataclass(frozen=True)
class StrikeRow:
    strike: float
    ce: OptionLeg
    pe: OptionLeg


@dataclass(frozen=True)
class OptionChainSnapshot:
    timestamp: datetime
    expiry: str
    spot: float
    rows: tuple[StrikeRow, ...]
    source: str = "NSE"
    raw_timestamp: str = ""


@dataclass(frozen=True)
class MarketContext:
    observed_at: datetime
    previous_spot: float | None = None
    recent_high: float | None = None
    recent_low: float | None = None
    momentum_rsi: float | None = None
    spot_volume_ratio: float | None = None


@dataclass(frozen=True)
class ScoreBreakdown:
    market_structure: float
    oi_positioning: float
    premium_confirmation: float
    location: float
    momentum: float
    volume: float
    risk_reward: float
    freshness: float

    @property
    def total(self) -> float:
        return round(
            self.market_structure
            + self.oi_positioning
            + self.premium_confirmation
            + self.location
            + self.momentum
            + self.volume
            + self.risk_reward
            + self.freshness,
            1,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "Market structure": self.market_structure,
            "OI positioning": self.oi_positioning,
            "Premium confirmation": self.premium_confirmation,
            "Location": self.location,
            "Momentum": self.momentum,
            "Volume": self.volume,
            "Risk-reward": self.risk_reward,
            "Freshness": self.freshness,
            "Total": self.total,
        }


@dataclass(frozen=True)
class TradeLevels:
    entry: float | None = None
    stop: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    support: float | None = None
    resistance: float | None = None
    reward_risk: float | None = None


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    score: float
    ce_score: float
    pe_score: float
    selected_strike: float | None
    market_state: str
    breakdown: ScoreBreakdown
    opposite_breakdown: ScoreBreakdown
    levels: TradeLevels
    reasons: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    generated_at: datetime | None = None
    data_timestamp: datetime | None = None

    @property
    def side(self) -> str:
        if self.action in {DecisionAction.BUY_CE, DecisionAction.PREPARE_CE}:
            return "CE"
        if self.action in {DecisionAction.BUY_PE, DecisionAction.PREPARE_PE}:
            return "PE"
        return "NONE"

    def to_record(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "score": self.score,
            "ce_score": self.ce_score,
            "pe_score": self.pe_score,
            "selected_strike": self.selected_strike,
            "market_state": self.market_state,
            "breakdown": self.breakdown.as_dict(),
            "opposite_breakdown": self.opposite_breakdown.as_dict(),
            "levels": {
                "entry": self.levels.entry,
                "stop": self.levels.stop,
                "target_1": self.levels.target_1,
                "target_2": self.levels.target_2,
                "support": self.levels.support,
                "resistance": self.levels.resistance,
                "reward_risk": self.levels.reward_risk,
            },
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "data_timestamp": self.data_timestamp.isoformat() if self.data_timestamp else None,
        }
