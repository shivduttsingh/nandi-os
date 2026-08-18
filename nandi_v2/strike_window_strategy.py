from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from statistics import median
from typing import Iterable

from nandi_oi.models import IntradayCandle, OptionStrikeCandles


class StrikeWindowSignal(str, Enum):
    CONFIRM_CE = "CONFIRM CE"
    CONFIRM_PE = "CONFIRM PE"
    WAIT = "WAIT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class StrikeWindowAssessment:
    signal: StrikeWindowSignal
    agreement_score: float
    nifty_change_pct: float | None
    ce_median_change_pct: float | None
    pe_median_change_pct: float | None
    ce_positive_strikes: int
    pe_positive_strikes: int
    dominant_strikes: int
    reason: str


def strike_offset_label(offset: int) -> str:
    labels = {
        -2: "2 strikes below ATM",
        -1: "1 strike below ATM",
        0: "ATM",
        1: "1 strike above ATM",
        2: "2 strikes above ATM",
    }
    return labels.get(offset, f"ATM {offset:+d} strikes")


def _by_timestamp(candles: Iterable[IntradayCandle]) -> dict[datetime, IntradayCandle]:
    return {item.timestamp: item for item in candles}


def assess_strike_window_confirmation(
    nifty_candles: Iterable[IntradayCandle],
    strike_series: Iterable[OptionStrikeCandles],
    *,
    lookback: int = 3,
    minimum_nifty_move_pct: float = 0.04,
    minimum_option_move_pct: float = 1.0,
    minimum_option_outperformance_pct: float = 0.75,
    minimum_breadth: int = 4,
) -> StrikeWindowAssessment:
    """Assess direction from NIFTY and robust premium breadth across ATM ±2.

    The median premium move prevents one cheap wing option from dominating the
    result. The score is agreement strength for paper validation, not a win
    probability and not an order instruction.
    """
    if lookback < 1:
        raise ValueError("Strike-window confirmation lookback must be positive")
    if not 1 <= minimum_breadth <= 5:
        raise ValueError("Strike-window breadth must be between one and five")

    series_by_offset = {item.offset: item for item in strike_series}
    required_offsets = set(range(-2, 3))
    if set(series_by_offset) != required_offsets:
        return StrikeWindowAssessment(
            signal=StrikeWindowSignal.UNAVAILABLE,
            agreement_score=0.0,
            nifty_change_pct=None,
            ce_median_change_pct=None,
            pe_median_change_pct=None,
            ce_positive_strikes=0,
            pe_positive_strikes=0,
            dominant_strikes=0,
            reason="Needs the complete ATM, two-strikes-below and two-strikes-above CE/PE window.",
        )

    nifty = _by_timestamp(nifty_candles)
    ce_maps = {
        offset: _by_timestamp(series_by_offset[offset].ce_candles)
        for offset in sorted(required_offsets)
    }
    pe_maps = {
        offset: _by_timestamp(series_by_offset[offset].pe_candles)
        for offset in sorted(required_offsets)
    }
    common_times = set(nifty)
    for candles in (*ce_maps.values(), *pe_maps.values()):
        common_times &= set(candles)
    ordered_times = sorted(common_times)
    if len(ordered_times) < lookback + 1:
        return StrikeWindowAssessment(
            signal=StrikeWindowSignal.UNAVAILABLE,
            agreement_score=0.0,
            nifty_change_pct=None,
            ce_median_change_pct=None,
            pe_median_change_pct=None,
            ce_positive_strikes=0,
            pe_positive_strikes=0,
            dominant_strikes=0,
            reason=(
                f"Needs at least {lookback + 1} matching completed candles across NIFTY "
                "and all ten ATM ±2 option charts."
            ),
        )

    start, end = ordered_times[-lookback - 1], ordered_times[-1]

    def change(items: dict[datetime, IntradayCandle]) -> float:
        opened = items[start].close
        return (items[end].close / opened - 1.0) * 100.0 if opened > 0 else 0.0

    nifty_move = change(nifty)
    ce_moves = [change(ce_maps[offset]) for offset in sorted(required_offsets)]
    pe_moves = [change(pe_maps[offset]) for offset in sorted(required_offsets)]
    ce_median = median(ce_moves)
    pe_median = median(pe_moves)
    ce_positive = sum(value > 0 for value in ce_moves)
    pe_positive = sum(value > 0 for value in pe_moves)
    ce_dominant = sum(ce > pe for ce, pe in zip(ce_moves, pe_moves))
    pe_dominant = sum(pe > ce for ce, pe in zip(ce_moves, pe_moves))

    if (
        nifty_move >= minimum_nifty_move_pct
        and ce_median >= minimum_option_move_pct
        and ce_median - pe_median >= minimum_option_outperformance_pct
        and ce_positive >= minimum_breadth
        and ce_dominant >= minimum_breadth
    ):
        signal = StrikeWindowSignal.CONFIRM_CE
        chosen_median, opposite_median = ce_median, pe_median
        chosen_positive, dominant = ce_positive, ce_dominant
        reason = (
            f"NIFTY is rising and CE premium breadth agrees across {ce_positive}/5 strikes; "
            f"CE outperforms PE at {ce_dominant}/5 matching strikes."
        )
    elif (
        nifty_move <= -minimum_nifty_move_pct
        and pe_median >= minimum_option_move_pct
        and pe_median - ce_median >= minimum_option_outperformance_pct
        and pe_positive >= minimum_breadth
        and pe_dominant >= minimum_breadth
    ):
        signal = StrikeWindowSignal.CONFIRM_PE
        chosen_median, opposite_median = pe_median, ce_median
        chosen_positive, dominant = pe_positive, pe_dominant
        reason = (
            f"NIFTY is falling and PE premium breadth agrees across {pe_positive}/5 strikes; "
            f"PE outperforms CE at {pe_dominant}/5 matching strikes."
        )
    else:
        return StrikeWindowAssessment(
            signal=StrikeWindowSignal.WAIT,
            agreement_score=0.0,
            nifty_change_pct=round(nifty_move, 3),
            ce_median_change_pct=round(ce_median, 3),
            pe_median_change_pct=round(pe_median, 3),
            ce_positive_strikes=ce_positive,
            pe_positive_strikes=pe_positive,
            dominant_strikes=max(ce_dominant, pe_dominant),
            reason=(
                "NIFTY direction and ATM ±2 premium breadth do not have clean four-of-five "
                "directional agreement."
            ),
        )

    index_strength = min(25.0, abs(nifty_move) / 0.20 * 25.0)
    option_strength = min(30.0, max(0.0, chosen_median) / 4.0 * 30.0)
    breadth_strength = chosen_positive / 5.0 * 25.0
    dominance_strength = min(
        20.0,
        max(0.0, chosen_median - opposite_median) / 4.0 * 20.0,
    )
    return StrikeWindowAssessment(
        signal=signal,
        agreement_score=round(
            index_strength + option_strength + breadth_strength + dominance_strength,
            1,
        ),
        nifty_change_pct=round(nifty_move, 3),
        ce_median_change_pct=round(ce_median, 3),
        pe_median_change_pct=round(pe_median, 3),
        ce_positive_strikes=ce_positive,
        pe_positive_strikes=pe_positive,
        dominant_strikes=dominant,
        reason=reason,
    )
