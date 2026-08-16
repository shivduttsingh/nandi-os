from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .models import Decision, DecisionAction


class TradeStatus(str, Enum):
    FLAT = "FLAT"
    PREPARE_CE = "PREPARE CE"
    PREPARE_PE = "PREPARE PE"
    ACTIVE_CE = "ACTIVE CE"
    ACTIVE_PE = "ACTIVE PE"
    HOLD = "HOLD"
    TRAIL = "TRAIL"
    PARTIAL = "BOOK PARTIAL"
    EXIT = "EXIT"


@dataclass(frozen=True)
class TradeState:
    status: TradeStatus = TradeStatus.FLAT
    side: str = "NONE"
    entry_spot: float | None = None
    stop_spot: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    selected_strike: float | None = None
    opened_at: datetime | None = None
    updated_at: datetime | None = None
    partial_booked: bool = False
    peak_favourable_spot: float | None = None
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.side in {"CE", "PE"} and self.status in {
            TradeStatus.ACTIVE_CE,
            TradeStatus.ACTIVE_PE,
            TradeStatus.HOLD,
            TradeStatus.TRAIL,
            TradeStatus.PARTIAL,
        }


def _prepare_status(side: str) -> TradeStatus:
    return TradeStatus.PREPARE_CE if side == "CE" else TradeStatus.PREPARE_PE


def _active_status(side: str) -> TradeStatus:
    return TradeStatus.ACTIVE_CE if side == "CE" else TradeStatus.ACTIVE_PE


def _favourable(side: str, current: float, reference: float) -> bool:
    return current > reference if side == "CE" else current < reference


def _stop_hit(side: str, spot: float, stop: float | None) -> bool:
    if stop is None:
        return False
    return spot <= stop if side == "CE" else spot >= stop


def _target_hit(side: str, spot: float, target: float | None) -> bool:
    if target is None:
        return False
    return spot >= target if side == "CE" else spot <= target


def _opposite_confirmed(decision: Decision, side: str) -> bool:
    if side == "CE":
        return decision.action == DecisionAction.BUY_PE
    return decision.action == DecisionAction.BUY_CE


def _elapsed_minutes(start: datetime | None, now: datetime) -> float:
    if start is None:
        return 0.0
    if start.tzinfo is None and now.tzinfo is not None:
        start = start.replace(tzinfo=now.tzinfo)
    elif start.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=start.tzinfo)
    return max(0.0, (now - start).total_seconds() / 60.0)


def advance_trade_state(
    state: TradeState,
    decision: Decision,
    spot: float,
    now: datetime,
    *,
    exit_score: float = 60.0,
    trail_after_target_1: bool = True,
    minimum_hold_minutes: float = 15.0,
    reversal_cooldown_minutes: float = 5.0,
) -> TradeState:
    """Advance one deterministic trade lifecycle step.

    The decision engine remains responsible for directional approval. This state
    machine only manages Prepare -> Active -> Hold/Trail/Partial -> Exit and never
    creates a new trade from a NO TRADE decision.
    """
    side = decision.side

    if not state.active:
        if state.status == TradeStatus.EXIT and state.updated_at is not None:
            cooldown_left = reversal_cooldown_minutes - _elapsed_minutes(state.updated_at, now)
            if cooldown_left > 0:
                return replace(
                    state,
                    reason=f"Reversal cooldown active; new CE/PE entries are blocked for {cooldown_left:.1f} more minute(s).",
                )
        if decision.action in {DecisionAction.PREPARE_CE, DecisionAction.PREPARE_PE}:
            return TradeState(
                status=_prepare_status(side),
                side=side,
                selected_strike=decision.selected_strike,
                updated_at=now,
                reason="Setup is developing but has not reached confirmed entry status.",
            )
        if decision.action in {DecisionAction.BUY_CE, DecisionAction.BUY_PE}:
            return TradeState(
                status=_active_status(side),
                side=side,
                entry_spot=spot,
                stop_spot=decision.levels.stop,
                target_1=decision.levels.target_1,
                target_2=decision.levels.target_2,
                selected_strike=decision.selected_strike,
                opened_at=now,
                updated_at=now,
                peak_favourable_spot=spot,
                reason="Confirmed Nandi entry.",
            )
        return TradeState(status=TradeStatus.FLAT, updated_at=now, reason="No confirmed trade.")

    active_side = state.side
    if _stop_hit(active_side, spot, state.stop_spot):
        return replace(state, status=TradeStatus.EXIT, updated_at=now, reason="Spot invalidation / stop-loss reached.")

    peak = state.peak_favourable_spot
    if peak is None or _favourable(active_side, spot, peak):
        peak = spot

    if _target_hit(active_side, spot, state.target_2):
        return replace(state, status=TradeStatus.EXIT, updated_at=now, peak_favourable_spot=peak, reason="Target 2 reached.")

    if _target_hit(active_side, spot, state.target_1):
        if not state.partial_booked:
            new_stop = state.entry_spot if trail_after_target_1 else state.stop_spot
            return replace(
                state,
                status=TradeStatus.PARTIAL,
                partial_booked=True,
                stop_spot=new_stop,
                updated_at=now,
                peak_favourable_spot=peak,
                reason="Target 1 reached; book partial and protect the remaining position.",
            )
        if trail_after_target_1:
            return replace(state, status=TradeStatus.TRAIL, updated_at=now, peak_favourable_spot=peak, reason="Hold remainder with trailing stop.")

    hold_left = minimum_hold_minutes - _elapsed_minutes(state.opened_at, now)
    if hold_left > 0:
        warning = " Opposite-side evidence is a warning only." if _opposite_confirmed(decision, active_side) else ""
        return replace(
            state,
            status=TradeStatus.HOLD,
            updated_at=now,
            peak_favourable_spot=peak,
            reason=f"Minimum {minimum_hold_minutes:g}-minute hold active; review in {hold_left:.1f} minute(s).{warning}",
        )

    if _opposite_confirmed(decision, active_side):
        return replace(state, status=TradeStatus.EXIT, updated_at=now, reason="Opposite-side setup confirmed after the minimum hold.")
    if decision.score < exit_score and decision.side not in {active_side, "NONE"}:
        return replace(state, status=TradeStatus.EXIT, updated_at=now, reason="Directional evidence deteriorated below exit threshold.")

    return replace(state, status=TradeStatus.HOLD, updated_at=now, peak_favourable_spot=peak, reason="Trade remains structurally valid.")
