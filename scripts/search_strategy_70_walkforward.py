from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import (
    _download_public_sample,
    _nearest_common_strike,
    _parse_option_frame,
    _parse_spot_frame,
    _row_to_candle,
    _update_aggregate,
)


@dataclass(frozen=True)
class FeatureEvent:
    day: date
    signal_time: datetime
    direction: str
    strike: int
    orb_minutes: int
    retest_distance_atr: float
    extension_atr: float
    option_premium: float
    option_volume_ratio: float
    option_outperformance_pct: float
    option_ema_aligned: bool
    option_oi_change_pct: float
    signal_option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    orb_minutes: int
    max_retest_atr: float
    max_extension_atr: float
    min_volume_ratio: float
    min_outperformance_pct: float
    require_ema: bool
    min_premium: float
    trigger_window: int
    target_points: float
    stop_points: float
    max_hold_minutes: int


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: datetime
    entry_time: datetime
    direction: str
    strike: int
    entry: float
    outcome: str
    net_points: float


@dataclass(frozen=True)
class Stats:
    trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float
    profitable_rate: float
    net_points: float
    expectancy: float
    profit_factor: float
    max_drawdown: float


def pct(start: float, end: float) -> float:
    return ((end / start) - 1.0) * 100.0 if start > 0 else 0.0


def atr(candles: list[IntradayCandle] | tuple[IntradayCandle, ...], lookback: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    previous = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    values: list[float] = []
    for candle in sample:
        values.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return mean(values) if values else 0.0


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def volume_ratio(candles: list[IntradayCandle], lookback: int = 12) -> float:
    if len(candles) < 4:
        return 0.0
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def move_pct(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def oi_change_pct(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    new = candles[-1].open_interest
    return pct(old, new) if old > 0 else 0.0


def opening_range(day_spot: tuple[IntradayCandle, ...], minutes: int) -> tuple[float, float] | None:
    session_start = datetime.combine(day_spot[0].timestamp.date(), time(9, 15))
    end = session_start + timedelta(minutes=minutes)
    sample = [c for c in day_spot if session_start <= c.timestamp < end]
    if len(sample) < max(8, minutes - 2):
        return None
    return max(c.high for c in sample), min(c.low for c in sample)


def build_events(
    spot_by_day: dict[date, tuple[IntradayCandle, ...]],
    option_rows_by_day: dict[date, list[object]],
) -> tuple[list[FeatureEvent], dict[tuple[date, str, int], tuple[IntradayCandle, ...]], dict[tuple[date, str, int], list[datetime]]]:
    events: list[FeatureEvent] = []
    all_series: dict[tuple[date, str, int], tuple[IntradayCandle, ...]] = {}
    all_times: dict[tuple[date, str, int], list[datetime]] = {}

    for day_value in sorted(spot_by_day):
        day_spot = spot_by_day[day_value]
        raw_options = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 100 or not raw_options:
            continue
        ranges = {m: opening_range(day_spot, m) for m in (15, 30)}
        if not any(ranges.values()):
            continue

        options_at_time: dict[datetime, list[tuple[str, object]]] = defaultdict(list)
        option_lists: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        strikes_by_side: dict[str, set[int]] = {"CE": set(), "PE": set()}
        for row in raw_options:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(candle)
            strikes_by_side[side].add(strike)
        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            continue

        for key, values in option_lists.items():
            series_key = (day_value, key[0], key[1])
            series = tuple(sorted(values, key=lambda c: c.timestamp))
            all_series[series_key] = series
            all_times[series_key] = [c.timestamp for c in series]

        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        option_history: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)

        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            for side, row in options_at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))

            if spot.timestamp.time() < time(9, 35) or spot.timestamp.time() > time(12, 15):
                continue
            if len(n1) < 25 or len(n5) < 4:
                continue
            spot_atr = atr(n1[-20:])
            if spot_atr <= 0:
                continue
            strike = _nearest_common_strike(strikes_by_side, spot.close)
            if strike is None:
                continue

            for orb_minutes, range_values in ranges.items():
                if range_values is None:
                    continue
                or_high, or_low = range_values
                end_time = (datetime.combine(day_value, time(9, 15)) + timedelta(minutes=orb_minutes + 5)).time()
                if spot.timestamp.time() < end_time:
                    continue

                recent = n1[-12:-1]
                bullish_break = any(c.close > or_high for c in recent)
                bearish_break = any(c.close < or_low for c in recent)
                current, previous = n1[-1], n1[-2]
                five = n5[-3:]

                direction = ""
                retest_norm = extension_norm = 999.0
                if (
                    bullish_break
                    and current.close > or_high
                    and current.close > current.open
                    and current.close > previous.close
                    and current.high > previous.high
                    and five[-1].close > five[-2].close > five[-3].close
                    and five[-1].close > or_high
                ):
                    direction = "CE"
                    retest_norm = max(0.0, current.low - or_high) / spot_atr
                    extension_norm = max(0.0, current.close - or_high) / spot_atr
                elif (
                    bearish_break
                    and current.close < or_low
                    and current.close < current.open
                    and current.close < previous.close
                    and current.low < previous.low
                    and five[-1].close < five[-2].close < five[-3].close
                    and five[-1].close < or_low
                ):
                    direction = "PE"
                    retest_norm = max(0.0, or_low - current.high) / spot_atr
                    extension_norm = max(0.0, or_low - current.close) / spot_atr
                if not direction:
                    continue

                chosen = option_history.get((direction, strike), [])
                opposite_side = "PE" if direction == "CE" else "CE"
                opposite = option_history.get((opposite_side, strike), [])
                if len(chosen) < 15 or len(opposite) < 5:
                    continue
                closes = [c.close for c in chosen[-20:]]
                ema_aligned = chosen[-1].close > ema(closes, 5) > ema(closes, 13)
                chosen_move = move_pct(chosen, 3)
                opposite_move = move_pct(opposite, 3)
                event = FeatureEvent(
                    day=day_value,
                    signal_time=spot.timestamp,
                    direction=direction,
                    strike=strike,
                    orb_minutes=orb_minutes,
                    retest_distance_atr=round(retest_norm, 4),
                    extension_atr=round(extension_norm, 4),
                    option_premium=chosen[-1].close,
                    option_volume_ratio=volume_ratio(chosen),
                    option_outperformance_pct=chosen_move - opposite_move,
                    option_ema_aligned=ema_aligned,
                    option_oi_change_pct=oi_change_pct(chosen, 3),
                    signal_option_high=chosen[-1].high,
                    series_key=(day_value, direction, strike),
                )
                events.append(event)

    events.sort(key=lambda e: e.signal_time)
    return events, all_series, all_times


def qualifies(event: FeatureEvent, candidate: Candidate) -> bool:
    if event.orb_minutes != candidate.orb_minutes:
        return False
    if event.retest_distance_atr > candidate.max_retest_atr:
        return False
    if event.extension_atr > candidate.max_extension_atr:
        return False
    if event.option_volume_ratio < candidate.min_volume_ratio:
        return False
    if event.option_outperformance_pct < candidate.min_outperformance_pct:
        return False
    if candidate.require_ema and not event.option_ema_aligned:
        return False
    if event.option_premium < candidate.min_premium:
        return False
    if event.option_oi_change_pct < -15.0:
        return False
    if event.signal_time.time() > time(11, 45):
        return False
    return True


def simulate(
    event: FeatureEvent,
    candidate: Candidate,
    series: tuple[IntradayCandle, ...],
    times: list[datetime],
    *,
    slippage: float = 0.20,
    friction: float = 0.50,
) -> Trade | None:
    from bisect import bisect_right

    start = bisect_right(times, event.signal_time)
    trigger = event.signal_option_high + 0.10
    deadline = event.signal_time + timedelta(minutes=candidate.trigger_window)
    entry_index = -1
    entry = 0.0
    for idx in range(start, len(series)):
        candle = series[idx]
        if candle.timestamp.date() != event.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            entry_index = idx
            entry = max(trigger, candle.open) + slippage
            break
    if entry_index < 0:
        return None

    stop = max(0.05, entry - candidate.stop_points)
    target = entry + candidate.target_points
    entry_time = series[entry_index].timestamp
    cutoff = entry_time + timedelta(minutes=candidate.max_hold_minutes)
    future = [c for c in series[entry_index:] if c.timestamp.date() == event.day and c.timestamp <= cutoff]
    if not future:
        return None
    for candle in future:
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
        if stop_hit:
            return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, round(entry, 2), "LOSS", round(-candidate.stop_points - friction, 2))
        if target_hit:
            return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, round(entry, 2), "WIN", round(candidate.target_points - friction, 2))
    last = future[-1]
    return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, round(entry, 2), "TIMEOUT", round(last.close - entry - friction, 2))


def evaluate(
    events: list[FeatureEvent],
    candidate: Candidate,
    series_map: dict[tuple[date, str, int], tuple[IntradayCandle, ...]],
    times_map: dict[tuple[date, str, int], list[datetime]],
    start_day: date,
    end_day: date,
) -> tuple[Stats, tuple[Trade, ...]]:
    by_day: dict[date, list[FeatureEvent]] = defaultdict(list)
    for event in events:
        if start_day <= event.day <= end_day and qualifies(event, candidate):
            by_day[event.day].append(event)

    trades: list[Trade] = []
    for day_value in sorted(by_day):
        # Maximum one trade per day. Try qualified setups chronologically until one actually triggers.
        for event in by_day[day_value]:
            series = series_map.get(event.series_key)
            times = times_map.get(event.series_key)
            if not series or not times:
                continue
            trade = simulate(event, candidate, series, times)
            if trade is not None:
                trades.append(trade)
                break

    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timeouts = sum(t.outcome == "TIMEOUT" for t in trades)
    total = len(trades)
    win_rate = 100.0 * wins / total if total else 0.0
    profitable_rate = 100.0 * sum(t.net_points > 0 for t in trades) / total if total else 0.0
    net = sum(t.net_points for t in trades)
    expectancy = net / total if total else 0.0
    gains = sum(max(0.0, t.net_points) for t in trades)
    losses_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / losses_value if losses_value > 0 else (gains if gains > 0 else 0.0)
    equity = peak = dd = 0.0
    for trade in trades:
        equity += trade.net_points
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    stats = Stats(
        trades=total,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        win_rate=round(win_rate, 2),
        profitable_rate=round(profitable_rate, 2),
        net_points=round(net, 2),
        expectancy=round(expectancy, 2),
        profit_factor=round(pf, 2),
        max_drawdown=round(dd, 2),
    )
    return stats, tuple(trades)


def candidate_grid() -> list[Candidate]:
    # The grid intentionally avoids pathological risk/reward combinations. Every target is at least 60% of the stop.
    candidates: list[Candidate] = []
    exit_pairs = [(4.0, 6.0), (5.0, 7.0), (6.0, 8.0), (6.0, 6.0), (8.0, 8.0), (8.0, 6.0)]
    for values in itertools.product(
        (15, 30),
        (0.20, 0.35, 0.55),
        (0.80, 1.10, 1.50),
        (0.80, 1.10, 1.40),
        (0.25, 0.75, 1.25),
        (False, True),
        (25.0, 40.0, 60.0),
        (1, 2, 3),
        exit_pairs,
        (20, 30),
    ):
        orb, retest, extension, vol, outperform, require_ema, premium, trigger, exit_pair, hold = values
        target, stop = exit_pair
        candidates.append(Candidate(orb, retest, extension, vol, outperform, require_ema, premium, trigger, target, stop, hold))
    return candidates


def lower_bound_wilson(wins: int, n: int, z: float = 1.0) -> float:
    # z=1.0 is used only for candidate ranking, not as a formal confidence claim.
    if n == 0:
        return 0.0
    p = wins / n
    denominator = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - margin) / denominator


def training_score(stats: Stats) -> float:
    if stats.trades < 25 or stats.expectancy <= 0 or stats.profit_factor <= 1.0:
        return -1e9
    return (
        lower_bound_wilson(stats.wins, stats.trades) * 100.0
        + min(stats.profit_factor, 3.0) * 5.0
        + min(stats.expectancy, 5.0) * 2.0
        - stats.max_drawdown / 50.0
    )


def proof_pass(stats: Stats) -> bool:
    return (
        stats.trades >= 14
        and stats.win_rate >= 70.0
        and stats.expectancy > 0
        and stats.profit_factor > 1.15
    )


def stress_pass(stats: Stats) -> bool:
    return (
        stats.trades >= 7
        and stats.win_rate >= 60.0
        and stats.expectancy > 0
        and stats.profit_factor > 1.0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="strategy_70_walkforward.json")
    args = parser.parse_args()

    path = _download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx"))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    min_day, max_day = date(2025, 7, 1), date(2026, 6, 30)
    spot_df = spot_df[(spot_df["timestamp"].dt.date >= min_day) & (spot_df["timestamp"].dt.date <= max_day)]
    opt_df = opt_df[(opt_df["day"] >= min_day) & (opt_df["day"] <= max_day)]

    spot_by_day: dict[date, tuple[IntradayCandle, ...]] = {}
    for day_value, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True):
        spot_by_day[day_value] = tuple(_row_to_candle(row) for row in group.itertuples(index=False))
    option_rows_by_day: dict[date, list[object]] = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows_by_day[row.day].append(row)

    events, series_map, times_map = build_events(spot_by_day, option_rows_by_day)
    candidates = candidate_grid()

    rounds = [
        # training, two-month untouched proof window, one-month stress window
        ((date(2025, 7, 1), date(2025, 9, 30)), (date(2025, 10, 1), date(2025, 11, 30)), (date(2025, 12, 1), date(2025, 12, 31))),
        ((date(2025, 7, 1), date(2025, 11, 30)), (date(2025, 12, 1), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28))),
        ((date(2025, 7, 1), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 30))),
        ((date(2025, 7, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 5, 31)), (date(2026, 6, 1), date(2026, 6, 30))),
    ]

    results: list[dict[str, object]] = []
    proven: dict[str, object] | None = None

    for round_number, (train_window, proof_window, stress_window) in enumerate(rounds, start=1):
        ranked: list[tuple[float, Candidate, Stats]] = []
        for candidate in candidates:
            stats, _ = evaluate(events, candidate, series_map, times_map, *train_window)
            score = training_score(stats)
            if score > -1e8:
                ranked.append((score, candidate, stats))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            results.append({"round": round_number, "status": "NO_TRAINING_CANDIDATE"})
            continue

        # Exactly one candidate is frozen before the proof window is evaluated.
        _, chosen, train_stats = ranked[0]
        proof_stats, proof_trades = evaluate(events, chosen, series_map, times_map, *proof_window)
        stress_stats, stress_trades = evaluate(events, chosen, series_map, times_map, *stress_window)
        passed = proof_pass(proof_stats) and stress_pass(stress_stats)
        round_result = {
            "round": round_number,
            "training_window": [train_window[0].isoformat(), train_window[1].isoformat()],
            "proof_window": [proof_window[0].isoformat(), proof_window[1].isoformat()],
            "stress_window": [stress_window[0].isoformat(), stress_window[1].isoformat()],
            "candidate_frozen_before_proof": True,
            "candidate": asdict(chosen),
            "training": asdict(train_stats),
            "proof": asdict(proof_stats),
            "stress": asdict(stress_stats),
            "proof_criteria": ">=14 proof trades, >=70% target wins, positive expectancy, PF>1.15; then >=7 stress trades, >=60% wins, positive expectancy, PF>1.0",
            "passed": passed,
            "proof_trades": [{**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat(), "entry_time": t.entry_time.isoformat()} for t in proof_trades],
            "stress_trades": [{**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat(), "entry_time": t.entry_time.isoformat()} for t in stress_trades],
        }
        results.append(round_result)
        if passed:
            proven = round_result
            break

    payload = {
        "search_name": "Shiv Strategy 70 Walk-Forward Search",
        "dataset": "rajmaurya0904/bhav public NIFTY + ATM options 1-minute snapshot",
        "candidate_count": len(candidates),
        "events_built": len(events),
        "anti_cherry_pick_rule": "Each round chooses exactly one candidate using only the training window. The next two months are then opened as proof. If it fails, that proof period is absorbed into later training and a later untouched period becomes the next proof window.",
        "status": "PROVEN_70_PLUS" if proven else "NO_70_PLUS_CANDIDATE_PROVEN",
        "proven_candidate": proven,
        "rounds": results,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
