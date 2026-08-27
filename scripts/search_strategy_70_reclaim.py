from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, time, timedelta
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
class Event:
    day: date
    signal_time: object
    direction: str
    strike: int
    spot_gap_atr: float
    spot_move_atr: float
    impulse_pct: float
    pullback_red: bool
    pullback_to_ema: bool
    outperformance_pct: float
    volume_ratio: float
    oi_change_pct: float
    body_ratio: float
    premium: float
    option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    min_spot_gap_atr: float
    min_spot_move_atr: float
    min_impulse_pct: float
    require_red_pullback: bool
    require_ema_pullback: bool
    min_outperformance_pct: float
    min_volume_ratio: float
    min_oi_change_pct: float
    min_body_ratio: float
    min_premium: float
    end_hour: int
    end_minute: int
    target_points: float
    stop_points: float
    max_hold_minutes: int


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: object
    direction: str
    strike: int
    outcome: str
    net_points: float


def pct(a, b):
    return ((b / a) - 1.0) * 100.0 if a > 0 else 0.0


def ema(values, period):
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    out = values[0]
    for value in values[1:]:
        out = alpha * value + (1.0 - alpha) * out
    return out


def atr(candles, lookback=14):
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    previous = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    values = []
    for candle in sample:
        values.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return mean(values) if values else 0.0


def move(candles, lookback=3):
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def volume_ratio(candles, lookback=12):
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def oi_change(candles, lookback=3):
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    return pct(old, candles[-1].open_interest) if old > 0 else 0.0


def build_events(spot_by_day, option_rows_by_day):
    events = []
    series_map = {}
    times_map = {}
    for day_value in sorted(spot_by_day):
        day_spot = spot_by_day[day_value]
        raw = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 100 or not raw:
            continue
        at_time = defaultdict(list)
        option_lists = defaultdict(list)
        strikes = {"CE": set(), "PE": set()}
        for row in raw:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(candle)
            strikes[side].add(strike)
        if not (strikes["CE"] & strikes["PE"]):
            continue
        for key, values in option_lists.items():
            sk = (day_value, key[0], key[1])
            series = tuple(sorted(values, key=lambda c: c.timestamp))
            series_map[sk] = series
            times_map[sk] = [c.timestamp for c in series]

        n1 = []
        n5 = []
        history = defaultdict(list)
        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            for side, row in at_time.get(spot.timestamp, []):
                history[(side, int(row.strike))].append(_row_to_candle(row))
            if not (time(9, 35) <= spot.timestamp.time() <= time(13, 0)):
                continue
            if len(n1) < 25 or len(n5) < 5:
                continue
            spot_atr = atr(n1[-20:])
            if spot_atr <= 0:
                continue
            closes5 = [c.close for c in n5[-12:]]
            fast5 = ema(closes5, 5)
            slow5 = ema(closes5, 9)
            gap = abs(fast5 - slow5) / spot_atr
            spot_move = (n1[-1].close - n1[-4].close) / spot_atr
            direction = ""
            if fast5 > slow5 and n5[-1].close > fast5 and spot_move > 0:
                direction = "CE"
            elif fast5 < slow5 and n5[-1].close < fast5 and spot_move < 0:
                direction = "PE"
                spot_move = -spot_move
            if not direction:
                continue
            strike = _nearest_common_strike(strikes, spot.close)
            if strike is None:
                continue
            chosen = history.get((direction, strike), [])
            opposite = history.get(("PE" if direction == "CE" else "CE", strike), [])
            if len(chosen) < 18 or len(opposite) < 6:
                continue
            prev = chosen[-2]
            current = chosen[-1]
            closes_before = [c.close for c in chosen[-18:-1]]
            fast_before = ema(closes_before, 5)
            slow_before = ema(closes_before, 13)
            impulse = pct(chosen[-7].close, chosen[-3].close)
            pullback_red = prev.close < prev.open
            pullback_to_ema = prev.low <= fast_before * 1.01 and prev.close >= slow_before * 0.98
            reclaim = current.close > current.open and current.close > prev.high and current.close > fast_before > slow_before
            if not reclaim:
                continue
            chosen_move = move(chosen, 3)
            opposite_move = move(opposite, 3)
            rng = max(current.high - current.low, 1e-9)
            body = (current.close - current.open) / rng
            events.append(Event(
                day_value, spot.timestamp, direction, strike, gap, spot_move, impulse,
                pullback_red, pullback_to_ema, chosen_move - opposite_move,
                volume_ratio(chosen), oi_change(chosen, 3), body, current.close,
                current.high, (day_value, direction, strike),
            ))
    events.sort(key=lambda e: e.signal_time)
    return events, series_map, times_map


def qualifies(e, c):
    return (
        e.spot_gap_atr >= c.min_spot_gap_atr
        and e.spot_move_atr >= c.min_spot_move_atr
        and e.impulse_pct >= c.min_impulse_pct
        and (not c.require_red_pullback or e.pullback_red)
        and (not c.require_ema_pullback or e.pullback_to_ema)
        and e.outperformance_pct >= c.min_outperformance_pct
        and e.volume_ratio >= c.min_volume_ratio
        and e.oi_change_pct >= c.min_oi_change_pct
        and e.body_ratio >= c.min_body_ratio
        and e.premium >= c.min_premium
        and e.signal_time.time() <= time(c.end_hour, c.end_minute)
    )


def simulate(e, c, series, times):
    start = bisect_right(times, e.signal_time)
    trigger = e.option_high + 0.10
    deadline = e.signal_time + timedelta(minutes=2)
    idx = -1
    entry = 0.0
    for i in range(start, len(series)):
        candle = series[i]
        if candle.timestamp.date() != e.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            idx = i
            entry = max(trigger, candle.open) + 0.20
            break
    if idx < 0:
        return None
    stop = max(0.05, entry - c.stop_points)
    target = entry + c.target_points
    cutoff = series[idx].timestamp + timedelta(minutes=c.max_hold_minutes)
    future = [x for x in series[idx:] if x.timestamp.date() == e.day and x.timestamp <= cutoff]
    if not future:
        return None
    for candle in future:
        if candle.low <= stop:
            return Trade(e.day, e.signal_time, e.direction, e.strike, "LOSS", -c.stop_points - 0.50)
        if candle.high >= target:
            return Trade(e.day, e.signal_time, e.direction, e.strike, "WIN", c.target_points - 0.50)
    net = future[-1].close - entry - 0.50
    return Trade(e.day, e.signal_time, e.direction, e.strike, "TIMEOUT", net)


def stats_for(trades):
    trades = tuple(trades)
    n = len(trades)
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timeouts = sum(t.outcome == "TIMEOUT" for t in trades)
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t.net_points
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        "trades": n, "wins": wins, "losses": losses, "timeouts": timeouts,
        "win_rate": round(100.0 * wins / n, 2) if n else 0.0,
        "net_points": round(net, 2), "expectancy": round(net / n, 2) if n else 0.0,
        "profit_factor": round(pf, 2), "max_drawdown": round(dd, 2),
    }


def evaluate(events, candidate, series_map, times_map, start_day, end_day):
    by_day = defaultdict(list)
    for event in events:
        if start_day <= event.day <= end_day and qualifies(event, candidate):
            by_day[event.day].append(event)
    trades = []
    for day_value in sorted(by_day):
        for event in by_day[day_value]:
            series = series_map.get(event.series_key)
            times = times_map.get(event.series_key)
            if not series or not times:
                continue
            trade = simulate(event, candidate, series, times)
            if trade is not None:
                trades.append(trade)
                break
    return stats_for(trades), tuple(trades)


def grid(selective=False):
    exits = ((6.0, 4.0, 25), (8.0, 5.0, 30), (10.0, 6.0, 35)) if selective else ((3.0, 4.0, 20), (4.0, 5.0, 25), (4.0, 4.0, 25))
    candidates = []
    for v in itertools.product(
        (0.02, 0.07) if not selective else (0.07, 0.12),
        (0.10, 0.30) if not selective else (0.30, 0.50),
        (0.5, 1.2, 2.0),
        (False, True),
        (False, True),
        (0.5, 1.2) if not selective else (1.2, 2.0),
        (0.9, 1.2) if not selective else (1.2, 1.5),
        (-8.0, 0.0),
        (0.35, 0.55) if not selective else (0.55, 0.70),
        (30.0, 50.0),
        ((11, 30), (13, 0)) if not selective else ((11, 0), (12, 0)),
        exits,
    ):
        candidates.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10][0],v[10][1],v[11][0],v[11][1],v[11][2]))
    return candidates


def rank_score(s, selective):
    minimum = 14 if selective else 25
    if s["trades"] < minimum or s["expectancy"] <= 0 or s["profit_factor"] <= 1.0:
        return -1e9
    p = s["wins"] / s["trades"]
    z = 1.0
    lower = (p + z*z/(2*s["trades"]) - z*math.sqrt((p*(1-p)+z*z/(4*s["trades"]))/s["trades"])) / (1 + z*z/s["trades"])
    return lower * 100 + min(s["profit_factor"], 4.0) * 5 + min(s["expectancy"], 5.0) * (4 if selective else 2) - s["max_drawdown"] / 60


def month_end(year, month):
    return date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)


def walk_forward(name, candidates, events, series_map, times_map, lo, selective):
    folds = []
    all_oos = []
    for year, month in ((2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)):
        start = date(year, month, 1)
        end = month_end(year, month)
        ranked = []
        for candidate in candidates:
            training, _ = evaluate(events, candidate, series_map, times_map, lo, start - timedelta(days=1))
            score = rank_score(training, selective)
            if score > -1e8:
                ranked.append((score, candidate, training))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked:
            folds.append({"month": start.strftime("%Y-%m"), "status": "NO_TRAINING_CANDIDATE"})
            continue
        _, candidate, training = ranked[0]
        testing, trades = evaluate(events, candidate, series_map, times_map, start, end)
        all_oos.extend(trades)
        folds.append({"month": start.strftime("%Y-%m"), "candidate_frozen_before_month": True, "candidate": asdict(candidate), "training": training, "test": testing, "trades": [{**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat()} for t in trades]})
    aggregate = stats_for(all_oos)
    positive = sum(1 for f in folds if "test" in f and f["test"]["expectancy"] > 0)
    sixty = sum(1 for f in folds if "test" in f and f["test"]["win_rate"] >= 60)
    active = sum(1 for f in folds if "test" in f and f["test"]["trades"] > 0)
    avg = aggregate["trades"] / active if active else 0.0
    if selective:
        passed = 16 <= aggregate["trades"] <= 36 and aggregate["win_rate"] >= 70 and aggregate["expectancy"] >= 1.5 and aggregate["profit_factor"] >= 1.5 and positive >= 5 and sixty >= 5 and avg <= 4.0
    else:
        passed = aggregate["trades"] >= 30 and aggregate["win_rate"] >= 70 and aggregate["expectancy"] > 0 and aggregate["profit_factor"] >= 1.2 and positive >= 5 and sixty >= 5
    return {"name": name, "status": "PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN", "aggregate_oos": aggregate, "positive_months": positive, "sixty_plus_months": sixty, "active_months": active, "avg_trades_per_active_month": round(avg, 2), "folds": folds}


def main():
    path = _download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx"))
    spot = _parse_spot_frame(path)
    options = _parse_option_frame(path)
    lo, hi = date(2025,7,1), date(2026,6,30)
    spot = spot[(spot.timestamp.dt.date >= lo) & (spot.timestamp.dt.date <= hi)]
    options = options[(options.day >= lo) & (options.day <= hi)]
    spot_by_day = {d: tuple(_row_to_candle(r) for r in group.itertuples(index=False)) for d, group in spot.groupby(spot.timestamp.dt.date, sort=True)}
    rows = defaultdict(list)
    for row in options.itertuples(index=False): rows[row.day].append(row)
    events, series_map, times_map = build_events(spot_by_day, rows)
    accuracy = walk_forward("Reclaim Accuracy", grid(False), events, series_map, times_map, lo, False)
    selective = walk_forward("Reclaim Selective Profit", grid(True), events, series_map, times_map, lo, True)
    payload = {"search_name": "Shiv Option Pullback-Reclaim Monthly Walk-Forward", "events_built": len(events), "accuracy": accuracy, "selective_profit": selective, "overall_status": "BOTH_PROVEN" if accuracy["status"] == "PROVEN_70_PLUS" and selective["status"] == "PROVEN_70_PLUS" else "NOT_BOTH_PROVEN"}
    Path("strategy_70_reclaim.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
