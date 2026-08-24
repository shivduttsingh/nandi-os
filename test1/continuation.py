from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Iterable

from nandi_oi.models import IntradayCandle


class Test1Signal(str, Enum):
    WAIT = "WAIT"
    PREPARE_CE = "PREPARE CE"
    PREPARE_PE = "PREPARE PE"
    CONFIRMED_CE = "CONFIRMED CE"
    CONFIRMED_PE = "CONFIRMED PE"
    LATE_SKIP_CE = "LATE / SKIP CE"
    LATE_SKIP_PE = "LATE / SKIP PE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Test1Assessment:
    signal: Test1Signal
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
    history = [max(0.0, c.volume) for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 1.0
    baseline = mean(history)
    return candles[-1].volume / baseline if baseline > 0 else 1.0


def _directional_structure(candles: tuple[IntradayCandle, ...]) -> int:
    if len(candles) < 3:
        return 0
    a, b, c = candles[-3:]
    bull = int(b.high > a.high and b.low >= a.low) + int(c.high > b.high and c.low >= b.low)
    bear = int(b.low < a.low and b.high <= a.high) + int(c.low < b.low and c.high <= b.high)
    if bull > bear:
        return bull
    if bear > bull:
        return -bear
    return 0


def _option_flow(chosen: tuple[IntradayCandle, ...], opposite: tuple[IntradayCandle, ...], lookback: int = 3) -> tuple[float, float]:
    if len(chosen) < lookback + 1 or len(opposite) < lookback + 1:
        return 0.0, 0.0
    chosen_move = _pct_change(chosen[-lookback - 1].close, chosen[-1].close)
    opposite_move = _pct_change(opposite[-lookback - 1].close, opposite[-1].close)
    return chosen_move, chosen_move - opposite_move


def _oi_delta(candles: tuple[IntradayCandle, ...], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    new = candles[-1].open_interest
    return _pct_change(old, new) if old > 0 else 0.0


def _move_consumed(candle: IntradayCandle, bullish: bool) -> float:
    span = max(candle.high - candle.low, 1e-9)
    progress = (candle.close - candle.low) / span if bullish else (candle.high - candle.close) / span
    return max(0.0, min(100.0, progress * 100.0))


def assess_test1_continuation(
    nifty_1m: Iterable[IntradayCandle],
    nifty_5m: Iterable[IntradayCandle],
    nifty_15m: Iterable[IntradayCandle],
    ce_1m: Iterable[IntradayCandle],
    pe_1m: Iterable[IntradayCandle],
    *,
    prepare_threshold: float = 72.0,
    confirm_threshold: float = 80.0,
    late_move_consumed_pct: float = 82.0,
) -> Test1Assessment:
    n1, n5, n15, ce, pe = map(_series, (nifty_1m, nifty_5m, nifty_15m, ce_1m, pe_1m))
    if min(len(n1), len(n5), len(n15), len(ce), len(pe)) < 4:
        return Test1Assessment(Test1Signal.UNAVAILABLE, "NONE", 0.0, 0.0, "UNKNOWN", 0.0, 0.0, 0.0, 0.0, 0.0, (), ("Need at least four live candles for all inputs.",))

    s1 = _directional_structure(n1)
    s5 = _directional_structure(n5)
    current_15, prior_15 = n15[-1], n15[-2]

    bull_price = (min(12.0, max(0, s1) * 6.0) + min(12.0, max(0, s5) * 6.0) + (6.0 if current_15.close > prior_15.high else 0.0))
    bear_price = (min(12.0, max(0, -s1) * 6.0) + min(12.0, max(0, -s5) * 6.0) + (6.0 if current_15.close < prior_15.low else 0.0))

    ce_move, ce_div = _option_flow(ce, pe)
    pe_move, pe_div = _option_flow(pe, ce)
    bull_premium = min(25.0, max(0.0, ce_move) * 3.0 + max(0.0, ce_div) * 2.0)
    bear_premium = min(25.0, max(0.0, pe_move) * 3.0 + max(0.0, pe_div) * 2.0)

    ce_oi, pe_oi = _oi_delta(ce), _oi_delta(pe)
    bull_oi = min(20.0, (min(10.0, pe_oi * 2.0) if pe_oi > 0 and pe_move < 0 else 0.0) + (min(10.0, abs(ce_oi) * 2.0) if ce_oi < 0 and ce_move > 0 else 0.0))
    bear_oi = min(20.0, (min(10.0, ce_oi * 2.0) if ce_oi > 0 and ce_move < 0 else 0.0) + (min(10.0, abs(pe_oi) * 2.0) if pe_oi < 0 and pe_move > 0 else 0.0))

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
    consumed = _move_consumed(current_15, bullish)
    late = consumed >= late_move_consumed_pct

    if score < prepare_threshold or abs(bull_total - bear_total) < 8.0:
        signal = Test1Signal.WAIT
    elif late:
        signal = Test1Signal.LATE_SKIP_CE if bullish else Test1Signal.LATE_SKIP_PE
    elif score >= confirm_threshold:
        signal = Test1Signal.CONFIRMED_CE if bullish else Test1Signal.CONFIRMED_PE
    else:
        signal = Test1Signal.PREPARE_CE if bullish else Test1Signal.PREPARE_PE

    reasons = (
        f"1m structure score {s1:+d}; 5m structure score {s5:+d}.",
        f"ATM CE move {ce_move:+.2f}%; ATM PE move {pe_move:+.2f}%.",
        f"ATM CE OI change {ce_oi:+.2f}%; ATM PE OI change {pe_oi:+.2f}%.",
        f"1m volume is {volume_ratio:.2f}x recent baseline.",
        f"15m move consumed {consumed:.1f}%.",
    )
    blockers = []
    if abs(bull_total - bear_total) < 8.0:
        blockers.append("CE/PE evidence gap is too small.")
    if late:
        blockers.append("Current 15m range is already heavily consumed; skip chase.")
    if volume_ratio < 1.0:
        blockers.append("Volume is below recent baseline.")

    return Test1Assessment(
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
        reasons=reasons,
        blockers=tuple(blockers),
    )
