from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable

from nandi_oi.models import IntradayCandle


class ATMConfirmationSignal(str, Enum):
    CONFIRM_CE = "CONFIRM CE"
    CONFIRM_PE = "CONFIRM PE"
    WAIT = "WAIT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ATMConfirmationAssessment:
    signal: ATMConfirmationSignal
    agreement_score: float
    nifty_change_pct: float | None
    ce_change_pct: float | None
    pe_change_pct: float | None
    reason: str


def _by_timestamp(candles: Iterable[IntradayCandle]) -> dict[datetime, IntradayCandle]:
    return {item.timestamp: item for item in candles}


def assess_atm_confirmation(
    nifty_candles: Iterable[IntradayCandle],
    ce_candles: Iterable[IntradayCandle],
    pe_candles: Iterable[IntradayCandle],
    *,
    lookback: int = 3,
    minimum_nifty_move_pct: float = 0.04,
    minimum_option_move_pct: float = 1.0,
    minimum_option_outperformance_pct: float = 0.5,
) -> ATMConfirmationAssessment:
    """Confirm NIFTY direction only when its ATM option premium agrees.

    The score measures agreement strength, not probability or expected win rate.
    """
    if lookback < 1:
        raise ValueError("ATM confirmation lookback must be positive")
    nifty = _by_timestamp(nifty_candles)
    ce = _by_timestamp(ce_candles)
    pe = _by_timestamp(pe_candles)
    common_times = sorted(set(nifty) & set(ce) & set(pe))
    if len(common_times) < lookback + 1:
        return ATMConfirmationAssessment(
            signal=ATMConfirmationSignal.UNAVAILABLE,
            agreement_score=0.0,
            nifty_change_pct=None,
            ce_change_pct=None,
            pe_change_pct=None,
            reason=f"Needs at least {lookback + 1} matching completed candles for NIFTY, ATM CE and ATM PE.",
        )
    start, end = common_times[-lookback - 1], common_times[-1]

    def change(items: dict[datetime, IntradayCandle]) -> float:
        opened = items[start].close
        return (items[end].close / opened - 1.0) * 100.0 if opened > 0 else 0.0

    nifty_move = change(nifty)
    ce_move = change(ce)
    pe_move = change(pe)

    if (
        nifty_move >= minimum_nifty_move_pct
        and ce_move >= minimum_option_move_pct
        and ce_move - pe_move >= minimum_option_outperformance_pct
    ):
        signal = ATMConfirmationSignal.CONFIRM_CE
        chosen_move, opposite_move = ce_move, pe_move
        reason = "NIFTY is rising and ATM CE premium is rising faster than ATM PE."
    elif (
        nifty_move <= -minimum_nifty_move_pct
        and pe_move >= minimum_option_move_pct
        and pe_move - ce_move >= minimum_option_outperformance_pct
    ):
        signal = ATMConfirmationSignal.CONFIRM_PE
        chosen_move, opposite_move = pe_move, ce_move
        reason = "NIFTY is falling and ATM PE premium is rising faster than ATM CE."
    else:
        return ATMConfirmationAssessment(
            signal=ATMConfirmationSignal.WAIT,
            agreement_score=0.0,
            nifty_change_pct=round(nifty_move, 3),
            ce_change_pct=round(ce_move, 3),
            pe_change_pct=round(pe_move, 3),
            reason="NIFTY and ATM option premiums do not have clean directional agreement.",
        )

    index_strength = min(30.0, abs(nifty_move) / 0.20 * 30.0)
    option_strength = min(40.0, max(0.0, chosen_move) / 4.0 * 40.0)
    divergence_strength = min(
        30.0,
        max(0.0, chosen_move - opposite_move) / 4.0 * 30.0,
    )
    return ATMConfirmationAssessment(
        signal=signal,
        agreement_score=round(index_strength + option_strength + divergence_strength, 1),
        nifty_change_pct=round(nifty_move, 3),
        ce_change_pct=round(ce_move, 3),
        pe_change_pct=round(pe_move, 3),
        reason=reason,
    )
