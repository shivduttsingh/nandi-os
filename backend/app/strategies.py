from dataclasses import dataclass
from typing import Literal

StrategyName = Literal["A", "B", "C"]

@dataclass
class MarketSnapshot:
    timestamp: str
    spot: float
    opening_range_high: float
    opening_range_low: float
    vwap: float
    volume_ratio: float
    close: float
    previous_close: float
    retest_confirmed: bool
    premium_confirmed: bool
    opposite_structure_break: bool = False

def evaluate_a(s: MarketSnapshot) -> dict:
    broke_up = s.close > s.opening_range_high
    broke_down = s.close < s.opening_range_low
    confirmation = s.volume_ratio >= 1.5 and s.retest_confirmed and s.premium_confirmed
    if broke_up and s.close > s.vwap and confirmation:
        return {"strategy": "A", "signal": "CE", "qualified": True, "reason": "Opening-range upside break with VWAP, volume, retest and premium confirmation."}
    if broke_down and s.close < s.vwap and confirmation:
        return {"strategy": "A", "signal": "PE", "qualified": True, "reason": "Opening-range downside break with VWAP, volume, retest and premium confirmation."}
    return {"strategy": "A", "signal": "NO TRADE", "qualified": False, "reason": "Breakout continuation rules are incomplete."}

def evaluate_b(s: MarketSnapshot) -> dict:
    failed_up = s.previous_close > s.opening_range_high and s.close < s.opening_range_high
    failed_down = s.previous_close < s.opening_range_low and s.close > s.opening_range_low
    if failed_up and s.opposite_structure_break and s.retest_confirmed and s.premium_confirmed:
        return {"strategy": "B", "signal": "PE", "qualified": True, "reason": "Upside breakout failed; downside structure break and retest confirmed."}
    if failed_down and s.opposite_structure_break and s.retest_confirmed and s.premium_confirmed:
        return {"strategy": "B", "signal": "CE", "qualified": True, "reason": "Downside breakout failed; upside structure break and retest confirmed."}
    return {"strategy": "B", "signal": "NO TRADE", "qualified": False, "reason": "Failed-breakout reversal rules are incomplete."}

def evaluate_c(s: MarketSnapshot) -> dict:
    a, b = evaluate_a(s), evaluate_b(s)
    if a["qualified"] and not b["qualified"]:
        return {"strategy": "C", "signal": a["signal"], "qualified": True, "selected": "A", "reason": "Adaptive selector chose Strategy A: " + a["reason"]}
    if b["qualified"] and not a["qualified"]:
        return {"strategy": "C", "signal": b["signal"], "qualified": True, "selected": "B", "reason": "Adaptive selector chose Strategy B: " + b["reason"]}
    if a["qualified"] and b["qualified"] and a["signal"] == b["signal"]:
        return {"strategy": "C", "signal": a["signal"], "qualified": True, "selected": "A+B", "reason": "Both strategies agree; shared confirmation present."}
    return {"strategy": "C", "signal": "NO TRADE", "qualified": False, "selected": None, "reason": "No unique, conflict-free setup. ATLAS will not force a trade."}

def evaluate(strategy: StrategyName, snapshot: MarketSnapshot) -> dict:
    return {"A": evaluate_a, "B": evaluate_b, "C": evaluate_c}[strategy](snapshot)
