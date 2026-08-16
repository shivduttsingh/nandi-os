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
    expiry: str = ""
    entry_premium: float | None = None
    current_premium: float | None = None
    stop_premium: float | None = None
    target_1_premium: float | None = None
    target_2_premium: float | None = None
    opened_at: datetime | None = None
    updated_at: datetime | None = None
    partial_booked: bool = False
    peak_favourable_spot: float | None = None
    peak_favourable_premium: float | None = None
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


def _premium_stop_hit(premium: float | None, stop: float | None) -> bool:
    return premium is not None and premium > 0 and stop is not None and premium <= stop


def _premium_target_hit(premium: float | None, target: float | None) -> bool:
    return premium is not None and premium > 0 and target is not None and premium >= target


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
    maximum_hold_minutes: float = 45.0,
    option_premium: float | None = None,
    expiry: str = "",
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
                expiry=expiry,
                current_premium=option_premium or decision.levels.option_ltp,
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
                expiry=expiry,
                entry_premium=decision.levels.option_entry or option_premium,
                current_premium=option_premium or decision.levels.option_ltp,
                stop_premium=decision.levels.option_stop,
                target_1_premium=decision.levels.option_target_1,
                target_2_premium=decision.levels.option_target_2,
                opened_at=now,
                updated_at=now,
                peak_favourable_spot=spot,
                peak_favourable_premium=option_premium,
                reason="Confirmed Nandi entry.",
            )
        return TradeState(status=TradeStatus.FLAT, updated_at=now, reason="No confirmed trade.")

    active_side = state.side
    current_premium = option_premium if option_premium is not None and option_premium > 0 else state.current_premium
    if _stop_hit(active_side, spot, state.stop_spot):
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, reason="NIFTY spot invalidation / stop-loss reached.")
    if _premium_stop_hit(current_premium, state.stop_premium):
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, reason="Option premium stop-loss reached.")

    peak = state.peak_favourable_spot
    if peak is None or _favourable(active_side, spot, peak):
        peak = spot
    peak_premium = state.peak_favourable_premium
    if current_premium is not None and (peak_premium is None or current_premium > peak_premium):
        peak_premium = current_premium

    if _target_hit(active_side, spot, state.target_2) or _premium_target_hit(current_premium, state.target_2_premium):
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason="Target 2 reached; exit the remaining position.")

    if _target_hit(active_side, spot, state.target_1) or _premium_target_hit(current_premium, state.target_1_premium):
        if not state.partial_booked:
            new_stop = state.entry_spot if trail_after_target_1 else state.stop_spot
            new_premium_stop = state.entry_premium if trail_after_target_1 else state.stop_premium
            return replace(
                state,
                status=TradeStatus.PARTIAL,
                partial_booked=True,
                stop_spot=new_stop,
                stop_premium=new_premium_stop,
                current_premium=current_premium,
                updated_at=now,
                peak_favourable_spot=peak,
                peak_favourable_premium=peak_premium,
                reason="Target 1 reached; book partial and protect the remaining position.",
            )
        if trail_after_target_1:
            return replace(state, status=TradeStatus.TRAIL, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason="Hold remainder with entry-protected trailing stop.")

    elapsed = _elapsed_minutes(state.opened_at, now)
    maximum_hold_minutes = max(float(minimum_hold_minutes), float(maximum_hold_minutes))
    if elapsed >= maximum_hold_minutes:
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason=f"Maximum {maximum_hold_minutes:g}-minute hold reached.")

    hold_left = minimum_hold_minutes - elapsed
    if hold_left > 0:
        warning = " Opposite-side evidence is a warning only." if _opposite_confirmed(decision, active_side) else ""
        return replace(
            state,
            status=TradeStatus.HOLD,
            current_premium=current_premium,
            updated_at=now,
            peak_favourable_spot=peak,
            peak_favourable_premium=peak_premium,
            reason=f"Minimum {minimum_hold_minutes:g}-minute hold active; review in {hold_left:.1f} minute(s).{warning}",
        )

    if _opposite_confirmed(decision, active_side):
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason="Opposite-side setup confirmed after the minimum hold.")
    if decision.score < exit_score and decision.side not in {active_side, "NONE"}:
        return replace(state, status=TradeStatus.EXIT, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason="Directional evidence deteriorated below exit threshold.")

    maximum_left = max(0.0, maximum_hold_minutes - elapsed)
    return replace(state, status=TradeStatus.HOLD, current_premium=current_premium, updated_at=now, peak_favourable_spot=peak, peak_favourable_premium=peak_premium, reason=f"Trade remains valid; maximum-time exit in {maximum_left:.1f} minute(s).")
