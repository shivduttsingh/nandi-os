from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from statistics import mean, median
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
    status_label: str = ""
    relative_premium_strength_pct: float | None = None
    weighted_dominance_pct: float = 0.0
    nifty_structure: str = "UNAVAILABLE"
    oi_confirmation: str = "UNAVAILABLE"
    volume_confirmation: str = "UNAVAILABLE"
    vwap_confirmation: str = "UNAVAILABLE"
    trend_efficiency: float | None = None
    persistence_bars: int = 0
    component_scores: tuple[tuple[str, float], ...] = tuple()
    blockers: tuple[str, ...] = tuple()


STRIKE_WEIGHTS = {
    -2: 0.15,
    -1: 0.20,
    0: 0.30,
    1: 0.20,
    2: 0.15,
}


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


def _percentage_change(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 else 0.0


def _weighted_sum(values: dict[int, float]) -> float:
    return sum(values[offset] * STRIKE_WEIGHTS[offset] for offset in STRIKE_WEIGHTS)


def _nifty_structure(candles: list[IntradayCandle]) -> str:
    """Classify the latest completed NIFTY structure without forecasting."""
    if len(candles) < 3:
        return "UNAVAILABLE"
    recent = candles[-3:]
    bullish_steps = sum(
        right.high > left.high for left, right in zip(recent[:-1], recent[1:])
    ) + sum(
        right.low >= left.low for left, right in zip(recent[:-1], recent[1:])
    )
    bearish_steps = sum(
        right.high < left.high for left, right in zip(recent[:-1], recent[1:])
    ) + sum(
        right.low <= left.low for left, right in zip(recent[:-1], recent[1:])
    )
    prior_high = max(item.high for item in recent[:-1])
    prior_low = min(item.low for item in recent[:-1])
    if recent[-1].close > prior_high or bullish_steps >= 3:
        return "BULLISH"
    if recent[-1].close < prior_low or bearish_steps >= 3:
        return "BEARISH"
    return "MIXED"


def _trend_efficiency(candles: list[IntradayCandle], lookback: int) -> float:
    """Kaufman-style efficiency ratio over the confirmation window."""
    window = candles[-lookback - 1:]
    if len(window) < 2:
        return 0.0
    net = abs(window[-1].close - window[0].close)
    path = sum(
        abs(right.close - left.close)
        for left, right in zip(window[:-1], window[1:])
    )
    return net / path if path > 0 else 0.0


def _vwap_direction(candles: list[IntradayCandle]) -> str:
    volumes = [max(0.0, float(item.volume)) for item in candles]
    total_volume = sum(volumes)
    if total_volume <= 0:
        return "UNAVAILABLE"
    vwap = sum(
        ((item.high + item.low + item.close) / 3.0) * volume
        for item, volume in zip(candles, volumes)
    ) / total_volume
    close = candles[-1].close
    if close > vwap:
        return "BULLISH"
    if close < vwap:
        return "BEARISH"
    return "NEUTRAL"


def _candidate_from_window(
    nifty_map: dict[datetime, IntradayCandle],
    ce_maps: dict[int, dict[datetime, IntradayCandle]],
    pe_maps: dict[int, dict[datetime, IntradayCandle]],
    ordered_times: list[datetime],
    end_index: int,
    lookback: int,
    minimum_nifty_move_pct: float,
    minimum_option_move_pct: float,
    minimum_option_outperformance_pct: float,
) -> str | None:
    """Return a directional candidate for one rolling endpoint."""
    if end_index - lookback < 0:
        return None
    start = ordered_times[end_index - lookback]
    end = ordered_times[end_index]

    def change(items: dict[datetime, IntradayCandle]) -> float:
        return _percentage_change(items[start].close, items[end].close)

    nifty_move = change(nifty_map)
    ce_moves = {offset: change(ce_maps[offset]) for offset in STRIKE_WEIGHTS}
    pe_moves = {offset: change(pe_maps[offset]) for offset in STRIKE_WEIGHTS}
    relative = _weighted_sum(ce_moves) - _weighted_sum(pe_moves)
    ce_dominance = sum(
        STRIKE_WEIGHTS[offset]
        for offset in STRIKE_WEIGHTS
        if ce_moves[offset] > pe_moves[offset]
    )
    pe_dominance = sum(
        STRIKE_WEIGHTS[offset]
        for offset in STRIKE_WEIGHTS
        if pe_moves[offset] > ce_moves[offset]
    )
    atm_ce = ce_moves[0]
    atm_pe = pe_moves[0]

    if (
        nifty_move >= minimum_nifty_move_pct
        and atm_ce >= minimum_option_move_pct
        and relative >= minimum_option_outperformance_pct
        and ce_dominance >= 0.55
    ):
        return "CE"
    if (
        nifty_move <= -minimum_nifty_move_pct
        and atm_pe >= minimum_option_move_pct
        and -relative >= minimum_option_outperformance_pct
        and pe_dominance >= 0.55
    ):
        return "PE"
    return None


def _oi_support(
    side: str,
    ce_moves: dict[int, float],
    pe_moves: dict[int, float],
    ce_maps: dict[int, dict[datetime, IntradayCandle]],
    pe_maps: dict[int, dict[datetime, IntradayCandle]],
    start: datetime,
    end: datetime,
) -> tuple[float | None, str]:
    weighted_support = 0.0
    available_weight = 0.0
    for offset, weight in STRIKE_WEIGHTS.items():
        ce_start = ce_maps[offset][start].open_interest
        ce_end = ce_maps[offset][end].open_interest
        pe_start = pe_maps[offset][start].open_interest
        pe_end = pe_maps[offset][end].open_interest
        if ce_start <= 0 and pe_start <= 0:
            continue

        strike_support = 0.0
        observations = 0
        if ce_start > 0:
            observations += 1
            ce_oi_change = _percentage_change(ce_start, ce_end)
            if side == "CE":
                if ce_moves[offset] > 0 and ce_oi_change > 0:
                    strike_support += 1.0
                elif ce_moves[offset] > 0 and ce_oi_change < 0:
                    strike_support += 0.5
            else:
                if ce_moves[offset] < 0 and ce_oi_change > 0:
                    strike_support += 1.0
                elif ce_moves[offset] < 0 and ce_oi_change < 0:
                    strike_support += 0.5
        if pe_start > 0:
            observations += 1
            pe_oi_change = _percentage_change(pe_start, pe_end)
            if side == "CE":
                if pe_moves[offset] < 0 and pe_oi_change > 0:
                    strike_support += 1.0
                elif pe_moves[offset] < 0 and pe_oi_change < 0:
                    strike_support += 0.5
            else:
                if pe_moves[offset] > 0 and pe_oi_change > 0:
                    strike_support += 1.0
                elif pe_moves[offset] > 0 and pe_oi_change < 0:
                    strike_support += 0.5

        if observations:
            available_weight += weight
            weighted_support += weight * (strike_support / observations)

    if available_weight <= 0:
        return None, "UNAVAILABLE"
    support = max(0.0, min(1.0, weighted_support / available_weight))
    if support >= 0.65:
        label = "SUPPORTS"
    elif support >= 0.40:
        label = "MIXED"
    else:
        label = "OPPOSES"
    return support, label


def _volume_support(
    side: str,
    ce_maps: dict[int, dict[datetime, IntradayCandle]],
    pe_maps: dict[int, dict[datetime, IntradayCandle]],
    ordered_times: list[datetime],
    lookback: int,
) -> tuple[float | None, str]:
    recent_times = ordered_times[-lookback - 1:]
    ratios: list[float] = []
    maps = ce_maps if side == "CE" else pe_maps
    for offset in STRIKE_WEIGHTS:
        volumes = [max(0.0, maps[offset][timestamp].volume) for timestamp in recent_times]
        if len(volumes) < 3 or sum(volumes) <= 0:
            continue
        baseline_values = volumes[:-1]
        baseline = mean(baseline_values) if any(baseline_values) else 0.0
        if baseline > 0:
            ratios.append(volumes[-1] / baseline)
    if not ratios:
        return None, "UNAVAILABLE"
    ratio = median(ratios)
    if ratio >= 1.10:
        label = "EXPANDING"
    elif ratio >= 0.80:
        label = "NORMAL"
    else:
        label = "WEAK"
    return ratio, label


def _empty_assessment(reason: str) -> StrikeWindowAssessment:
    return StrikeWindowAssessment(
        signal=StrikeWindowSignal.UNAVAILABLE,
        agreement_score=0.0,
        nifty_change_pct=None,
        ce_median_change_pct=None,
        pe_median_change_pct=None,
        ce_positive_strikes=0,
        pe_positive_strikes=0,
        dominant_strikes=0,
        reason=reason,
        status_label="UNAVAILABLE",
    )


def assess_strike_window_confirmation(
    nifty_candles: Iterable[IntradayCandle],
    strike_series: Iterable[OptionStrikeCandles],
    *,
    lookback: int = 3,
    minimum_nifty_move_pct: float = 0.04,
    minimum_option_move_pct: float = 1.0,
    minimum_option_outperformance_pct: float = 0.75,
    minimum_breadth: int = 4,
    persistence_bars: int = 2,
    confirmation_score: float = 65.0,
    strong_score: float = 75.0,
    a_plus_score: float = 85.0,
) -> StrikeWindowAssessment:
    """Assess ATM ±2 direction using a multi-layer, paper-only confirmation model.

    The 0-100 score measures setup quality, not win probability. It combines
    ATM premium (20), weighted ATM ±2 agreement (20), NIFTY structure (20),
    option OI activity (15), option volume (10), NIFTY VWAP when available (5),
    trend efficiency (5), and rolling signal persistence (5).

    The original ATM-only strategy is intentionally independent of this module.
    """
    if lookback < 1:
        raise ValueError("Strike-window confirmation lookback must be positive")
    if not 1 <= minimum_breadth <= 5:
        raise ValueError("Strike-window breadth must be between one and five")
    if persistence_bars < 1:
        raise ValueError("Strike-window persistence bars must be positive")
    if not 0 < confirmation_score <= strong_score <= a_plus_score <= 100:
        raise ValueError("Strike-window score thresholds must be ordered within 0-100")

    series_by_offset = {item.offset: item for item in strike_series}
    required_offsets = set(range(-2, 3))
    if set(series_by_offset) != required_offsets:
        return _empty_assessment(
            "Needs the complete ATM, two-strikes-below and two-strikes-above CE/PE window."
        )

    nifty_map = _by_timestamp(nifty_candles)
    ce_maps = {
        offset: _by_timestamp(series_by_offset[offset].ce_candles)
        for offset in sorted(required_offsets)
    }
    pe_maps = {
        offset: _by_timestamp(series_by_offset[offset].pe_candles)
        for offset in sorted(required_offsets)
    }
    common_times = set(nifty_map)
    for candles in (*ce_maps.values(), *pe_maps.values()):
        common_times &= set(candles)
    ordered_times = sorted(common_times)
    minimum_history = lookback + persistence_bars
    if len(ordered_times) < minimum_history:
        return _empty_assessment(
            f"Needs at least {minimum_history} matching completed candles across NIFTY "
            "and all ten ATM ±2 option charts for the persistence filter."
        )

    start = ordered_times[-lookback - 1]
    end = ordered_times[-1]

    def change(items: dict[datetime, IntradayCandle]) -> float:
        return _percentage_change(items[start].close, items[end].close)

    nifty_move = change(nifty_map)
    ce_moves = {offset: change(ce_maps[offset]) for offset in sorted(required_offsets)}
    pe_moves = {offset: change(pe_maps[offset]) for offset in sorted(required_offsets)}
    ce_values = [ce_moves[offset] for offset in sorted(required_offsets)]
    pe_values = [pe_moves[offset] for offset in sorted(required_offsets)]
    ce_median = median(ce_values)
    pe_median = median(pe_values)
    ce_positive = sum(value > 0 for value in ce_values)
    pe_positive = sum(value > 0 for value in pe_values)
    ce_dominant = sum(ce_moves[offset] > pe_moves[offset] for offset in STRIKE_WEIGHTS)
    pe_dominant = sum(pe_moves[offset] > ce_moves[offset] for offset in STRIKE_WEIGHTS)
    weighted_ce = _weighted_sum(ce_moves)
    weighted_pe = _weighted_sum(pe_moves)
    relative = weighted_ce - weighted_pe
    ce_weighted_dominance = sum(
        STRIKE_WEIGHTS[offset]
        for offset in STRIKE_WEIGHTS
        if ce_moves[offset] > pe_moves[offset]
    )
    pe_weighted_dominance = sum(
        STRIKE_WEIGHTS[offset]
        for offset in STRIKE_WEIGHTS
        if pe_moves[offset] > ce_moves[offset]
    )

    nifty_bars = [nifty_map[timestamp] for timestamp in ordered_times]
    structure = _nifty_structure(nifty_bars)
    efficiency = _trend_efficiency(nifty_bars, lookback)
    vwap = _vwap_direction(nifty_bars)

    # Premium-first candidate lets Nandi explain conflicts instead of flipping sides.
    premium_side: str | None = None
    if (
        ce_moves[0] >= minimum_option_move_pct
        and relative >= minimum_option_outperformance_pct
        and ce_weighted_dominance >= 0.55
        and ce_positive >= 3
    ):
        premium_side = "CE"
    elif (
        pe_moves[0] >= minimum_option_move_pct
        and -relative >= minimum_option_outperformance_pct
        and pe_weighted_dominance >= 0.55
        and pe_positive >= 3
    ):
        premium_side = "PE"

    nifty_side: str | None = None
    if nifty_move >= minimum_nifty_move_pct:
        nifty_side = "CE"
    elif nifty_move <= -minimum_nifty_move_pct:
        nifty_side = "PE"

    if premium_side is None:
        status = "SIDEWAYS — NO TRADE" if (
            abs(nifty_move) < minimum_nifty_move_pct and efficiency < 0.35
        ) else "WAIT — LOW MOMENTUM"
        return StrikeWindowAssessment(
            signal=StrikeWindowSignal.WAIT,
            agreement_score=0.0,
            nifty_change_pct=round(nifty_move, 3),
            ce_median_change_pct=round(ce_median, 3),
            pe_median_change_pct=round(pe_median, 3),
            ce_positive_strikes=ce_positive,
            pe_positive_strikes=pe_positive,
            dominant_strikes=max(ce_dominant, pe_dominant),
            reason="ATM and weighted ATM ±2 premiums do not have a strong directional edge.",
            status_label=status,
            relative_premium_strength_pct=round(relative, 3),
            weighted_dominance_pct=round(
                max(ce_weighted_dominance, pe_weighted_dominance) * 100.0, 1
            ),
            nifty_structure=structure,
            vwap_confirmation=vwap,
            trend_efficiency=round(efficiency, 3),
            blockers=("No premium-side candidate",),
        )

    side = premium_side
    chosen_moves = ce_moves if side == "CE" else pe_moves
    opposite_moves = pe_moves if side == "CE" else ce_moves
    chosen_positive = ce_positive if side == "CE" else pe_positive
    dominant = ce_dominant if side == "CE" else pe_dominant
    weighted_dominance = (
        ce_weighted_dominance if side == "CE" else pe_weighted_dominance
    )
    chosen_atm = chosen_moves[0]
    opposite_atm = opposite_moves[0]
    signed_relative = relative if side == "CE" else -relative

    blockers: list[str] = []
    if nifty_side is None:
        blockers.append("NIFTY move has not confirmed direction")
    elif nifty_side != side:
        blockers.append(f"NIFTY move conflicts with {side} premium direction")
    if structure == ("BEARISH" if side == "CE" else "BULLISH"):
        blockers.append("NIFTY market structure conflicts with premium direction")
    if chosen_positive < minimum_breadth:
        blockers.append(
            f"Only {chosen_positive}/5 {side} premiums are positive; {minimum_breadth}/5 required"
        )
    if weighted_dominance < 0.70:
        blockers.append(
            f"Weighted {side} strike dominance is below the 70% confirmation floor"
        )

    # 20 points: ATM premium confirmation.
    atm_edge = max(0.0, chosen_atm - opposite_atm)
    atm_score = min(
        20.0,
        10.0 * min(1.0, max(0.0, chosen_atm) / max(minimum_option_move_pct, 0.01))
        + 10.0 * min(
            1.0,
            atm_edge / max(minimum_option_outperformance_pct, 0.01),
        ),
    )

    # 20 points: weighted cluster agreement, favouring ATM and ±1 over the wings.
    breadth_ratio = chosen_positive / 5.0
    cluster_score = 20.0 * min(
        1.0,
        0.60 * weighted_dominance + 0.40 * breadth_ratio,
    )

    # 20 points: structure; mixed structure gets partial credit only when NIFTY move agrees.
    wanted_structure = "BULLISH" if side == "CE" else "BEARISH"
    if structure == wanted_structure:
        structure_score = 20.0
    elif structure == "MIXED" and nifty_side == side:
        structure_score = 10.0
    else:
        structure_score = 0.0

    # 15 points: OI activity around all five strikes.
    oi_support, oi_label = _oi_support(
        side, ce_moves, pe_moves, ce_maps, pe_maps, start, end
    )
    oi_score = 0.0 if oi_support is None else 15.0 * oi_support

    # 10 points: chosen-side volume expansion / participation.
    volume_ratio, volume_label = _volume_support(
        side, ce_maps, pe_maps, ordered_times, lookback
    )
    if volume_ratio is None:
        volume_score = 0.0
    elif volume_ratio >= 1.10:
        volume_score = 10.0
    elif volume_ratio >= 0.80:
        volume_score = 5.0
    else:
        volume_score = 0.0

    # 5 points: underlying VWAP filter, only when Upstox provides usable index volume.
    wanted_vwap = "BULLISH" if side == "CE" else "BEARISH"
    vwap_score = 5.0 if vwap == wanted_vwap else 0.0

    # 5 points: trend efficiency. Choppy paths get little or no credit.
    trend_score = 5.0 * min(1.0, efficiency / 0.60)

    # 5 points: require the same rolling direction to persist to prevent CE/PE flip-flops.
    rolling_candidates: list[str | None] = []
    for end_index in range(
        len(ordered_times) - persistence_bars,
        len(ordered_times),
    ):
        rolling_candidates.append(
            _candidate_from_window(
                nifty_map,
                ce_maps,
                pe_maps,
                ordered_times,
                end_index,
                lookback,
                minimum_nifty_move_pct,
                minimum_option_move_pct,
                minimum_option_outperformance_pct,
            )
        )
    persisted = all(candidate == side for candidate in rolling_candidates)
    persistence_score = 5.0 if persisted else 0.0
    if not persisted:
        blockers.append(
            f"{side} has not persisted for {persistence_bars} consecutive completed evaluations"
        )

    component_scores = (
        ("ATM premium", round(atm_score, 1)),
        ("Weighted ATM ±2", round(cluster_score, 1)),
        ("NIFTY structure", round(structure_score, 1)),
        ("OI confirmation", round(oi_score, 1)),
        ("Volume", round(volume_score, 1)),
        ("VWAP", round(vwap_score, 1)),
        ("Trend strength", round(trend_score, 1)),
        ("Persistence", round(persistence_score, 1)),
    )
    total_score = round(sum(value for _, value in component_scores), 1)

    hard_confirmation = (
        nifty_side == side
        and structure != ("BEARISH" if side == "CE" else "BULLISH")
        and chosen_positive >= minimum_breadth
        and weighted_dominance >= 0.70
        and signed_relative >= minimum_option_outperformance_pct
        and persisted
    )

    if hard_confirmation and total_score >= confirmation_score:
        signal = (
            StrikeWindowSignal.CONFIRM_CE
            if side == "CE"
            else StrikeWindowSignal.CONFIRM_PE
        )
        if total_score >= a_plus_score:
            status = f"A+ {side} SETUP"
        elif total_score >= strong_score:
            status = f"STRONG {side}"
        else:
            status = f"CONFIRM {side}"
        reason = (
            f"{side} passed weighted ATM ±2 breadth, NIFTY direction/structure and "
            f"{persistence_bars}-evaluation persistence. Setup quality is {total_score:.1f}/100."
        )
    else:
        signal = StrikeWindowSignal.WAIT
        conflict = any("conflicts" in item for item in blockers)
        if conflict:
            status = "WAIT — CONFLICTING EVIDENCE"
        elif nifty_side is None or efficiency < 0.35:
            status = f"WAIT — {side} DEVELOPING / LOW MOMENTUM"
        else:
            status = f"WAIT — {side} DEVELOPING"
        reason = (
            f"{side} is the premium-side candidate, but confirmation is incomplete. "
            + (
                "; ".join(blockers)
                if blockers
                else f"Setup score {total_score:.1f} is below {confirmation_score:.0f}."
            )
        )

    return StrikeWindowAssessment(
        signal=signal,
        agreement_score=total_score,
        nifty_change_pct=round(nifty_move, 3),
        ce_median_change_pct=round(ce_median, 3),
        pe_median_change_pct=round(pe_median, 3),
        ce_positive_strikes=ce_positive,
        pe_positive_strikes=pe_positive,
        dominant_strikes=dominant,
        reason=reason,
        status_label=status,
        relative_premium_strength_pct=round(relative, 3),
        weighted_dominance_pct=round(weighted_dominance * 100.0, 1),
        nifty_structure=structure,
        oi_confirmation=oi_label,
        volume_confirmation=volume_label,
        vwap_confirmation=vwap,
        trend_efficiency=round(efficiency, 3),
        persistence_bars=(
            persistence_bars
            if persisted
            else sum(1 for candidate in reversed(rolling_candidates) if candidate == side)
        ),
        component_scores=component_scores,
        blockers=tuple(blockers),
    )
