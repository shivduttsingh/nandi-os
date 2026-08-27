from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean, median
from typing import Iterable

from nandi_oi.models import IntradayCandle


class StrategyBSignal(str, Enum):
    WAIT = "WAIT"
    WATCH_CE = "WATCH CE"
    WATCH_PE = "WATCH PE"
    TRADE_CE = "TRADE CE"
    TRADE_PE = "TRADE PE"
    BLOCKED_CE = "BLOCKED CE"
    BLOCKED_PE = "BLOCKED PE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class StrategyBAssessment:
    signal: StrategyBSignal
    direction: str
    score: float
    opposite_score: float
    score_gap: float
    structure_score: float
    oi_score: float
    premium_score: float
    volume_score: float
    breakout_score: float
    momentum_score: float
    confirmation_score: float
    premium_move_pct: float
    opposite_premium_move_pct: float
    chosen_oi_change_pct: float
    opposite_oi_change_pct: float
    option_volume_ratio: float
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]


def _series(values: Iterable[IntradayCandle]) -> tuple[IntradayCandle, ...]:
    return tuple(sorted(values, key=lambda c: c.timestamp))


def _pct(start: float, end: float) -> float:
    return ((end / start) - 1.0) * 100.0 if start > 0 else 0.0


def _move(candles: tuple[IntradayCandle, ...], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return _pct(candles[-lookback - 1].close, candles[-1].close)


def _oi_change(candles: tuple[IntradayCandle, ...], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    new = candles[-1].open_interest
    return _pct(old, new) if old > 0 else 0.0


def _volume_ratio(candles: tuple[IntradayCandle, ...], lookback: int = 10) -> float:
    if len(candles) < 3:
        return 1.0
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 1.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 1.0


def _atr(candles: tuple[IntradayCandle, ...], lookback: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    ranges = []
    prev_close = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    for candle in sample:
        ranges.append(max(candle.high - candle.low, abs(candle.high - prev_close), abs(candle.low - prev_close)))
        prev_close = candle.close
    return mean(ranges) if ranges else 0.0


def _structure_score(n5: tuple[IntradayCandle, ...], n15: tuple[IntradayCandle, ...], bullish: bool) -> float:
    if len(n5) < 3 or len(n15) < 2:
        return 0.0
    a, b, c = n5[-3:]
    if bullish:
        five = sum((b.high > a.high, b.low >= a.low, c.high > b.high, c.low >= b.low)) * 2.5
        fifteen = 5.0 if n15[-1].close > n15[-2].close else 0.0
        fifteen += 5.0 if n15[-1].close > (n15[-2].high + n15[-2].low) / 2.0 else 0.0
    else:
        five = sum((b.low < a.low, b.high <= a.high, c.low < b.low, c.high <= b.high)) * 2.5
        fifteen = 5.0 if n15[-1].close < n15[-2].close else 0.0
        fifteen += 5.0 if n15[-1].close < (n15[-2].high + n15[-2].low) / 2.0 else 0.0
    return min(20.0, five + fifteen)


def _breakout_score(n1: tuple[IntradayCandle, ...], bullish: bool) -> float:
    if len(n1) < 9:
        return 0.0
    history = n1[-9:-1]
    current = n1[-1]
    if bullish:
        breakout = current.close > max(c.high for c in history)
        retest_hold = current.low <= max(c.high for c in history[-4:]) and current.close > current.open
    else:
        breakout = current.close < min(c.low for c in history)
        retest_hold = current.high >= min(c.low for c in history[-4:]) and current.close < current.open
    if breakout:
        return 10.0
    if retest_hold:
        return 6.0
    return 0.0


def _momentum_score(n1: tuple[IntradayCandle, ...], n5: tuple[IntradayCandle, ...], bullish: bool) -> float:
    if len(n1) < 4 or len(n5) < 2:
        return 0.0
    one_move = n1[-1].close - n1[-4].close
    five_move = n5[-1].close - n5[-2].close
    score = 0.0
    if bullish:
        score += 5.0 if one_move > 0 else 0.0
        score += 5.0 if five_move > 0 else 0.0
    else:
        score += 5.0 if one_move < 0 else 0.0
        score += 5.0 if five_move < 0 else 0.0
    return score


def _confirmation_score(n1: tuple[IntradayCandle, ...], bullish: bool) -> float:
    if len(n1) < 2:
        return 0.0
    prev, current = n1[-2], n1[-1]
    if bullish:
        body_confirm = prev.close > prev.open and current.close > current.open
        progression = current.close > prev.close and current.low >= prev.low
    else:
        body_confirm = prev.close < prev.open and current.close < current.open
        progression = current.close < prev.close and current.high <= prev.high
    return (5.0 if body_confirm else 0.0) + (5.0 if progression else 0.0)


def _premium_score(chosen_move: float, opposite_move: float) -> float:
    relative = chosen_move - opposite_move
    score = 0.0
    if chosen_move >= 0.6:
        score += 5.0
    if chosen_move >= 1.2:
        score += 4.0
    if relative >= 1.0:
        score += 3.0
    if relative >= 2.0:
        score += 3.0
    return min(15.0, score)


def _oi_score(chosen_move: float, chosen_oi: float, opposite_move: float, opposite_oi: float) -> float:
    score = 0.0
    # Chosen option price + OI rising = long build-up; price up + OI falling = short covering.
    if chosen_move > 0 and chosen_oi > 0:
        score += 12.0
    elif chosen_move > 0 and chosen_oi < 0:
        score += 8.0
    # Opposite option price falling while OI rises is consistent with writing on the opposite side.
    if opposite_move < 0 and opposite_oi > 0:
        score += 8.0
    elif opposite_move < 0 and opposite_oi < 0:
        score += 4.0
    return min(20.0, score)


def _option_volume_score(ratio: float) -> float:
    if ratio >= 1.50:
        return 15.0
    if ratio >= 1.20:
        return 10.0
    if ratio >= 1.00:
        return 5.0
    return 0.0


def _side_scores(
    n1: tuple[IntradayCandle, ...],
    n5: tuple[IntradayCandle, ...],
    n15: tuple[IntradayCandle, ...],
    chosen: tuple[IntradayCandle, ...],
    opposite: tuple[IntradayCandle, ...],
    *,
    bullish: bool,
) -> tuple[float, tuple[float, ...]]:
    chosen_move = _move(chosen)
    opposite_move = _move(opposite)
    chosen_oi = _oi_change(chosen)
    opposite_oi = _oi_change(opposite)
    vol_ratio = _volume_ratio(chosen)
    parts = (
        _structure_score(n5, n15, bullish),
        _oi_score(chosen_move, chosen_oi, opposite_move, opposite_oi),
        _premium_score(chosen_move, opposite_move),
        _option_volume_score(vol_ratio),
        _breakout_score(n1, bullish),
        _momentum_score(n1, n5, bullish),
        _confirmation_score(n1, bullish),
    )
    return min(100.0, sum(parts)), parts


def assess_strategy_b(
    nifty_1m: Iterable[IntradayCandle],
    nifty_5m: Iterable[IntradayCandle],
    nifty_15m: Iterable[IntradayCandle],
    ce_1m: Iterable[IntradayCandle],
    pe_1m: Iterable[IntradayCandle],
    *,
    trade_threshold: float = 88.0,
    watch_threshold: float = 78.0,
    min_score_gap: float = 10.0,
) -> StrategyBAssessment:
    n1, n5, n15, ce, pe = map(_series, (nifty_1m, nifty_5m, nifty_15m, ce_1m, pe_1m))
    if min(len(n1), len(n5), len(n15), len(ce), len(pe)) < 4:
        return StrategyBAssessment(
            StrategyBSignal.UNAVAILABLE, "NONE", 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 1.0, (), ("Need at least four synchronized candles for all inputs.",)
        )

    ce_total, ce_parts = _side_scores(n1, n5, n15, ce, pe, bullish=True)
    pe_total, pe_parts = _side_scores(n1, n5, n15, pe, ce, bullish=False)
    bullish = ce_total >= pe_total
    direction = "CE" if bullish else "PE"
    chosen, opposite = (ce, pe) if bullish else (pe, ce)
    total = ce_total if bullish else pe_total
    opposite_total = pe_total if bullish else ce_total
    parts = ce_parts if bullish else pe_parts
    gap = total - opposite_total

    chosen_move = _move(chosen)
    opposite_move = _move(opposite)
    chosen_oi = _oi_change(chosen)
    opposite_oi = _oi_change(opposite)
    vol_ratio = _volume_ratio(chosen)

    blockers: list[str] = []
    if gap < min_score_gap:
        blockers.append(f"CE/PE score separation is only {gap:.1f}; need at least {min_score_gap:.1f}.")
    if chosen_move <= 0:
        blockers.append("Chosen option premium is not rising over the recent 3-minute window.")
    if parts[6] < 10.0:
        blockers.append("Two-candle confirmation is incomplete.")
    if vol_ratio < 0.85:
        blockers.append("Chosen option volume is below its recent baseline.")

    latest_time = n1[-1].timestamp.time()
    if latest_time.hour == 9 and latest_time.minute < 25:
        blockers.append("Opening noise filter is active before 09:25.")
    if latest_time.hour > 14 or (latest_time.hour == 14 and latest_time.minute > 45):
        blockers.append("No new Strategy B entries after 14:45.")

    atr = _atr(n1)
    if len(n1) >= 4 and atr > 0:
        directional_move = (n1[-1].close - n1[-4].close) if bullish else (n1[-4].close - n1[-1].close)
        if directional_move > 1.8 * atr:
            blockers.append("Anti-chase filter: the latest 3-minute spot move is stretched versus ATR.")

    if total >= trade_threshold and not blockers:
        signal = StrategyBSignal.TRADE_CE if bullish else StrategyBSignal.TRADE_PE
    elif total >= trade_threshold and blockers:
        signal = StrategyBSignal.BLOCKED_CE if bullish else StrategyBSignal.BLOCKED_PE
    elif total >= watch_threshold and gap >= min_score_gap:
        signal = StrategyBSignal.WATCH_CE if bullish else StrategyBSignal.WATCH_PE
    else:
        signal = StrategyBSignal.WAIT

    reasons = (
        f"Structure {parts[0]:.1f}/20; OI {parts[1]:.1f}/20; premium {parts[2]:.1f}/15.",
        f"Option volume {parts[3]:.1f}/15; breakout/retest {parts[4]:.1f}/10.",
        f"Momentum {parts[5]:.1f}/10; two-candle confirmation {parts[6]:.1f}/10.",
        f"Chosen premium move {chosen_move:+.2f}% vs opposite {opposite_move:+.2f}%.",
        f"Chosen OI change {chosen_oi:+.2f}% vs opposite {opposite_oi:+.2f}%.",
        f"Chosen option volume is {vol_ratio:.2f}x its recent median.",
    )

    return StrategyBAssessment(
        signal=signal,
        direction=direction,
        score=round(total, 1),
        opposite_score=round(opposite_total, 1),
        score_gap=round(gap, 1),
        structure_score=round(parts[0], 1),
        oi_score=round(parts[1], 1),
        premium_score=round(parts[2], 1),
        volume_score=round(parts[3], 1),
        breakout_score=round(parts[4], 1),
        momentum_score=round(parts[5], 1),
        confirmation_score=round(parts[6], 1),
        premium_move_pct=round(chosen_move, 2),
        opposite_premium_move_pct=round(opposite_move, 2),
        chosen_oi_change_pct=round(chosen_oi, 2),
        opposite_oi_change_pct=round(opposite_oi, 2),
        option_volume_ratio=round(vol_ratio, 2),
        reasons=reasons,
        blockers=tuple(blockers),
    )
