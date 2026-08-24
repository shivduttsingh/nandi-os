from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Iterable

from nandi_oi.models import IntradayCandle


class A5Signal(str, Enum):
    WAIT = "WAIT"
    PREPARE_CE = "PREPARE CE"
    PREPARE_PE = "PREPARE PE"
    CONFIRMED_CE = "CONFIRMED CE"
    CONFIRMED_PE = "CONFIRMED PE"
    LATE_SKIP_CE = "LATE / SKIP CE"
    LATE_SKIP_PE = "LATE / SKIP PE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class A5Assessment:
    signal: A5Signal
    direction: str
    score: float
    move_consumed_pct: float
    late_entry_risk: str
    price_structure_score: float
    premium_flow_score: float
    oi_score: float
    volume_score: float
    acceptance_score: float
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]


def _series(values: Iterable[IntradayCandle]) -> tuple[IntradayCandle, ...]:
    return tuple(sorted(values, key=lambda c: c.timestamp))


def _pct_change(start: float, end: float) -> float:
    return ((end / start) - 1.0) * 100.0 if start > 0 else 0.0


def _recent_volume_ratio(candles: tuple[IntradayCandle, ...], lookback: int = 10) -> float:
    if len(candles) < 2:
        return 1.0
    history = [max(0.0, c.volume) for c in candles[-lookback - 1 : -1] if c.volume > 0]
    if not history:
        return 1.0
    baseline = mean(history)
    return candles[-1].volume / baseline if baseline > 0 else 1.0


def _directional_structure(candles: tuple[IntradayCandle, ...]) -> tuple[int, tuple[str, ...]]:
    """Return -2..+2 from the latest three candles without future data."""
    if len(candles) < 3:
        return 0, ("Not enough candles for micro-structure.",)
    a, b, c = candles[-3:]
    bull = int(b.high > a.high and b.low >= a.low) + int(c.high > b.high and c.low >= b.low)
    bear = int(b.low < a.low and b.high <= a.high) + int(c.low < b.low and c.high <= b.high)
    if bull > bear:
        return bull, ("HH/HL structure is developing.",)
    if bear > bull:
        return -bear, ("LH/LL structure is developing.",)
    return 0, ("Micro-structure is mixed.",)


def _option_flow(
    chosen: tuple[IntradayCandle, ...],
    opposite: tuple[IntradayCandle, ...],
    lookback: int = 3,
) -> tuple[float, float, float, tuple[str, ...]]:
    if len(chosen) < lookback + 1 or len(opposite) < lookback + 1:
        return 0.0, 0.0, 0.0, ("Insufficient option premium history.",)
    chosen_move = _pct_change(chosen[-lookback - 1].close, chosen[-1].close)
    opposite_move = _pct_change(opposite[-lookback - 1].close, opposite[-1].close)
    divergence = chosen_move - opposite_move
    reasons = (
        f"Selected premium move {chosen_move:+.2f}%.",
        f"Opposite premium move {opposite_move:+.2f}%.",
        f"Premium divergence {divergence:+.2f} pts.",
    )
    return chosen_move, opposite_move, divergence, reasons


def _oi_delta(candles: tuple[IntradayCandle, ...], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    new = candles[-1].open_interest
    if old <= 0:
        return 0.0
    return _pct_change(old, new)


def _move_consumed(candle: IntradayCandle, bullish: bool) -> float:
    span = max(candle.high - candle.low, 1e-9)
    if bullish:
        progress = (candle.close - candle.low) / span
    else:
        progress = (candle.high - candle.close) / span
    return max(0.0, min(100.0, progress * 100.0))


def assess_a5_continuation(
    nifty_1m: Iterable[IntradayCandle],
    nifty_5m: Iterable[IntradayCandle],
    nifty_15m: Iterable[IntradayCandle],
    ce_1m: Iterable[IntradayCandle],
    pe_1m: Iterable[IntradayCandle],
    *,
    prepare_threshold: float = 72.0,
    confirm_threshold: float = 80.0,
    late_move_consumed_pct: float = 82.0,
) -> A5Assessment:
    """Evaluate a live, no-lookahead continuation setup.

    Score is an evidence/confluence score, not a calibrated win probability.
    The latest available candles are intentionally used so this engine can react
    before a 15-minute candle has closed. It must therefore be validated with
    timestamp-accurate replay before production sizing decisions are made.
    """
    n1, n5, n15, ce, pe = map(_series, (nifty_1m, nifty_5m, nifty_15m, ce_1m, pe_1m))
    if min(len(n1), len(n5), len(n15), len(ce), len(pe)) < 4:
        return A5Assessment(
            signal=A5Signal.UNAVAILABLE,
            direction="NONE",
            score=0.0,
            move_consumed_pct=0.0,
            late_entry_risk="UNKNOWN",
            price_structure_score=0.0,
            premium_flow_score=0.0,
            oi_score=0.0,
            volume_score=0.0,
            acceptance_score=0.0,
            reasons=(),
            blockers=("Need at least four live candles for NIFTY 1m/5m/15m and ATM CE/PE 1m.",),
        )

    s1, r1 = _directional_structure(n1)
    s5, r5 = _directional_structure(n5)
    current_15 = n15[-1]
    prior_15 = n15[-2]

    bull_price = 0.0
    bear_price = 0.0
    if s1 > 0:
        bull_price += min(12.0, s1 * 6.0)
    elif s1 < 0:
        bear_price += min(12.0, abs(s1) * 6.0)
    if s5 > 0:
        bull_price += min(12.0, s5 * 6.0)
    elif s5 < 0:
        bear_price += min(12.0, abs(s5) * 6.0)
    if current_15.close > prior_15.high:
        bull_price += 6.0
    if current_15.close < prior_15.low:
        bear_price += 6.0

    ce_move, pe_move, ce_div, ce_reasons = _option_flow(ce, pe)
    pe_move2, ce_move2, pe_div, pe_reasons = _option_flow(pe, ce)
    bull_premium = max(0.0, min(25.0, (max(0.0, ce_move) * 3.0) + (max(0.0, ce_div) * 2.0)))
    bear_premium = max(0.0, min(25.0, (max(0.0, pe_move2) * 3.0) + (max(0.0, pe_div) * 2.0)))

    ce_oi = _oi_delta(ce)
    pe_oi = _oi_delta(pe)
    # OI is deliberately confirmatory: premium + OI relationships are ambiguous by themselves.
    bull_oi = 0.0
    bear_oi = 0.0
    if pe_oi > 0 and pe_move < 0:
        bull_oi += min(10.0, pe_oi * 2.0)
    if ce_oi < 0 and ce_move > 0:
        bull_oi += min(10.0, abs(ce_oi) * 2.0)
    if ce_oi > 0 and ce_move < 0:
        bear_oi += min(10.0, ce_oi * 2.0)
    if pe_oi < 0 and pe_move2 > 0:
        bear_oi += min(10.0, abs(pe_oi) * 2.0)
    bull_oi = min(20.0, bull_oi)
    bear_oi = min(20.0, bear_oi)

    volume_ratio = _recent_volume_ratio(n1)
    bull_volume = 15.0 if volume_ratio >= 1.5 and n1[-1].close > n1[-1].open else (8.0 if volume_ratio >= 1.15 and n1[-1].close > n1[-1].open else 0.0)
    bear_volume = 15.0 if volume_ratio >= 1.5 and n1[-1].close < n1[-1].open else (8.0 if volume_ratio >= 1.15 and n1[-1].close < n1[-1].open else 0.0)

    span = max(current_15.high - current_15.low, 1e-9)
    close_pos = (current_15.close - current_15.low) / span
    bull_acceptance = 10.0 if close_pos >= 0.70 else (5.0 if close_pos >= 0.58 else 0.0)
    bear_acceptance = 10.0 if close_pos <= 0.30 else (5.0 if close_pos <= 0.42 else 0.0)

    bull_total = min(100.0, bull_price + bull_premium + bull_oi + bull_volume + bull_acceptance)
    bear_total = min(100.0, bear_price + bear_premium + bear_oi + bear_volume + bear_acceptance)

    bullish = bull_total > bear_total
    score = bull_total if bullish else bear_total
    direction = "CE" if bullish else "PE"
    consumed = _move_consumed(current_15, bullish=bullish)
    late = consumed >= late_move_consumed_pct

    if score < prepare_threshold or abs(bull_total - bear_total) < 8.0:
        signal = A5Signal.WAIT
    elif late:
        signal = A5Signal.LATE_SKIP_CE if bullish else A5Signal.LATE_SKIP_PE
    elif score >= confirm_threshold:
        signal = A5Signal.CONFIRMED_CE if bullish else A5Signal.CONFIRMED_PE
    else:
        signal = A5Signal.PREPARE_CE if bullish else A5Signal.PREPARE_PE

    selected_reasons = list(r1 + r5)
    selected_reasons.extend(ce_reasons if bullish else pe_reasons)
    selected_reasons.append(f"1m volume is {volume_ratio:.2f}x its recent baseline.")
    selected_reasons.append(f"ATM CE OI change {ce_oi:+.2f}%; ATM PE OI change {pe_oi:+.2f}%.")
    selected_reasons.append(f"15m directional move consumed {consumed:.1f}%.")

    blockers: list[str] = []
    if abs(bull_total - bear_total) < 8.0:
        blockers.append("CE/PE evidence gap is too small; direction is mixed.")
    if late:
        blockers.append("Most of the current 15m range is already consumed; do not chase.")
    if volume_ratio < 1.0:
        blockers.append("Volume is below its recent baseline.")

    return A5Assessment(
        signal=signal,
        direction=direction,
        score=round(score, 1),
        move_consumed_pct=round(consumed, 1),
        late_entry_risk="HIGH" if late else ("MEDIUM" if consumed >= 65 else "LOW"),
        price_structure_score=round(bull_price if bullish else bear_price, 1),
        premium_flow_score=round(bull_premium if bullish else bear_premium, 1),
        oi_score=round(bull_oi if bullish else bear_oi, 1),
        volume_score=round(bull_volume if bullish else bear_volume, 1),
        acceptance_score=round(bull_acceptance if bullish else bear_acceptance, 1),
        reasons=tuple(selected_reasons),
        blockers=tuple(blockers),
    )
