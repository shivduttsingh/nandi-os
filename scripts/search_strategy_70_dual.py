from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
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
class Event:
    day: date
    signal_time: datetime
    direction: str
    strike: int
    breakout_5: bool
    breakout_10: bool
    trend_gap_atr: float
    spot_move_3_atr: float
    fifteen_aligned: bool
    option_move_1_pct: float
    option_move_3_pct: float
    option_outperformance_pct: float
    option_volume_ratio: float
    option_oi_change_pct: float
    option_ema_aligned: bool
    option_body_ratio: float
    option_premium: float
    option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    breakout_lookback: int
    min_trend_gap_atr: float
    min_spot_move_3_atr: float
    require_fifteen: bool
    min_option_move_3_pct: float
    min_outperformance_pct: float
    min_volume_ratio: float
    min_oi_change_pct: float
    require_option_ema: bool
    min_option_body_ratio: float
    min_premium: float
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
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
    outcome: str
    net_points: float


@dataclass(frozen=True)
class Stats:
    trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float
    net_points: float
    expectancy: float
    profit_factor: float
    max_drawdown: float


def pct(a: float, b: float) -> float:
    return ((b / a) - 1.0) * 100.0 if a > 0 else 0.0


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    out = values[0]
    for value in values[1:]:
        out = alpha * value + (1.0 - alpha) * out
    return out


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


def volume_ratio(candles: list[IntradayCandle], lookback: int = 12) -> float:
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def option_move(candles: list[IntradayCandle], lookback: int) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def oi_change(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    return pct(old, candles[-1].open_interest) if old > 0 else 0.0


def build_events(spot_by_day, option_rows_by_day):
    events: list[Event] = []
    series_map: dict[tuple[date, str, int], tuple[IntradayCandle, ...]] = {}
    times_map: dict[tuple[date, str, int], list[datetime]] = {}

    for day_value in sorted(spot_by_day):
        day_spot = spot_by_day[day_value]
        raw_options = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 100 or not raw_options:
            continue

        options_at_time = defaultdict(list)
        option_lists = defaultdict(list)
        strikes_by_side = {"CE": set(), "PE": set()}
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
            series_map[series_key] = series
            times_map[series_key] = [c.timestamp for c in series]

        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        n15: list[IntradayCandle] = []
        option_history = defaultdict(list)

        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            _update_aggregate(n15, spot, 15)
            for side, row in options_at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))

            if not (time(9, 35) <= spot.timestamp.time() <= time(13, 15)):
                continue
            if len(n1) < 25 or len(n5) < 5 or len(n15) < 3:
                continue
            spot_atr = atr(n1[-20:])
            if spot_atr <= 0:
                continue

            closes5 = [c.close for c in n5[-12:]]
            fast5 = ema(closes5, 5)
            slow5 = ema(closes5, 9)
            trend_gap = abs(fast5 - slow5) / spot_atr
            current = n1[-1]
            previous = n1[-2]
            move3 = (current.close - n1[-4].close) / spot_atr
            prior5 = n1[-6:-1]
            prior10 = n1[-11:-1]

            direction = ""
            breakout5 = breakout10 = False
            fifteen_aligned = False
            if fast5 > slow5 and current.close > current.open and current.close > previous.close:
                breakout5 = current.close > max(c.high for c in prior5)
                breakout10 = current.close > max(c.high for c in prior10)
                if breakout5:
                    direction = "CE"
                    fifteen_aligned = n15[-2].close > n15[-3].close
            elif fast5 < slow5 and current.close < current.open and current.close < previous.close:
                breakout5 = current.close < min(c.low for c in prior5)
                breakout10 = current.close < min(c.low for c in prior10)
                if breakout5:
                    direction = "PE"
                    move3 = -move3
                    fifteen_aligned = n15[-2].close < n15[-3].close
            if not direction:
                continue

            strike = _nearest_common_strike(strikes_by_side, spot.close)
            if strike is None:
                continue
            chosen = option_history.get((direction, strike), [])
            opposite_side = "PE" if direction == "CE" else "CE"
            opposite = option_history.get((opposite_side, strike), [])
            if len(chosen) < 15 or len(opposite) < 5:
                continue

            move1 = option_move(chosen, 1)
            move3opt = option_move(chosen, 3)
            opposite3 = option_move(opposite, 3)
            closes_opt = [c.close for c in chosen[-20:]]
            opt_fast = ema(closes_opt, 5)
            opt_slow = ema(closes_opt, 13)
            rng = max(chosen[-1].high - chosen[-1].low, 1e-9)
            body_ratio = abs(chosen[-1].close - chosen[-1].open) / rng

            events.append(
                Event(
                    day=day_value,
                    signal_time=spot.timestamp,
                    direction=direction,
                    strike=strike,
                    breakout_5=breakout5,
                    breakout_10=breakout10,
                    trend_gap_atr=trend_gap,
                    spot_move_3_atr=max(0.0, move3),
                    fifteen_aligned=fifteen_aligned,
                    option_move_1_pct=move1,
                    option_move_3_pct=move3opt,
                    option_outperformance_pct=move3opt - opposite3,
                    option_volume_ratio=volume_ratio(chosen),
                    option_oi_change_pct=oi_change(chosen, 3),
                    option_ema_aligned=chosen[-1].close > opt_fast > opt_slow,
                    option_body_ratio=body_ratio,
                    option_premium=chosen[-1].close,
                    option_high=chosen[-1].high,
                    series_key=(day_value, direction, strike),
                )
            )

    events.sort(key=lambda e: e.signal_time)
    return events, series_map, times_map


def qualifies(event: Event, candidate: Candidate) -> bool:
    if candidate.breakout_lookback == 10 and not event.breakout_10:
        return False
    if candidate.breakout_lookback == 5 and not event.breakout_5:
        return False
    if event.trend_gap_atr < candidate.min_trend_gap_atr:
        return False
    if event.spot_move_3_atr < candidate.min_spot_move_3_atr:
        return False
    if candidate.require_fifteen and not event.fifteen_aligned:
        return False
    if event.option_move_3_pct < candidate.min_option_move_3_pct:
        return False
    if event.option_outperformance_pct < candidate.min_outperformance_pct:
        return False
    if event.option_volume_ratio < candidate.min_volume_ratio:
        return False
    if event.option_oi_change_pct < candidate.min_oi_change_pct:
        return False
    if candidate.require_option_ema and not event.option_ema_aligned:
        return False
    if event.option_body_ratio < candidate.min_option_body_ratio:
        return False
    if event.option_premium < candidate.min_premium:
        return False
    t = event.signal_time.time()
    if t < time(candidate.start_hour, candidate.start_minute) or t > time(candidate.end_hour, candidate.end_minute):
        return False
    return True


def simulate(event: Event, candidate: Candidate, series, times):
    start = bisect_right(times, event.signal_time)
    trigger = event.option_high + 0.10
    deadline = event.signal_time + timedelta(minutes=2)
    entry_index = -1
    entry = 0.0
    for index in range(start, len(series)):
        candle = series[index]
        if candle.timestamp.date() != event.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            entry_index = index
            entry = max(candle.open, trigger) + 0.20
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
        if candle.low <= stop:
            return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, "LOSS", -candidate.stop_points - 0.50)
        if candle.high >= target:
            return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, "WIN", candidate.target_points - 0.50)
    return Trade(event.day, event.signal_time, entry_time, event.direction, event.strike, "TIMEOUT", future[-1].close - entry - 0.50)


def stats_for(trades) -> Stats:
    trades = tuple(trades)
    total = len(trades)
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timeouts = sum(t.outcome == "TIMEOUT" for t in trades)
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade.net_points
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return Stats(
        total, wins, losses, timeouts,
        round(100.0 * wins / total, 2) if total else 0.0,
        round(net, 2), round(net / total, 2) if total else 0.0,
        round(pf, 2), round(drawdown, 2),
    )


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


def accuracy_grid():
    exits = [(4.0, 5.0, 15), (4.0, 5.0, 25), (5.0, 5.0, 25)]
    out = []
    for v in itertools.product(
        (5, 10), (0.02, 0.06), (0.10, 0.30), (False, True),
        (0.3, 0.8), (0.8, 1.5), (0.9, 1.2), (-8.0, 0.0),
        (False, True), (0.30, 0.55), (30.0, 50.0),
        ((9, 35), (9, 50)), ((11, 30), (13, 0)), exits,
    ):
        out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11][0],v[11][1],v[12][0],v[12][1],v[13][0],v[13][1],v[13][2]))
    return out


def selective_grid():
    exits = [(6.0, 4.0, 25), (8.0, 5.0, 30), (10.0, 6.0, 35)]
    out = []
    for v in itertools.product(
        (10,), (0.06, 0.10), (0.30, 0.50), (True,),
        (0.8, 1.4), (1.5, 2.5), (1.2, 1.5), (0.0, 2.0),
        (True,), (0.55, 0.70), (40.0, 60.0),
        ((9, 50), (10, 5)), ((11, 15), (12, 0)), exits,
    ):
        out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11][0],v[11][1],v[12][0],v[12][1],v[13][0],v[13][1],v[13][2]))
    return out


def rank_score(stats: Stats, selective: bool) -> float:
    minimum = 14 if selective else 25
    if stats.trades < minimum or stats.expectancy <= 0 or stats.profit_factor <= 1.0:
        return -1e9
    p = stats.wins / stats.trades
    z = 1.0
    lower = (p + z*z/(2*stats.trades) - z*math.sqrt((p*(1-p)+z*z/(4*stats.trades))/stats.trades)) / (1 + z*z/stats.trades)
    return lower * 100 + min(stats.profit_factor, 4.0) * 5 + min(stats.expectancy, 5.0) * (4 if selective else 2) - stats.max_drawdown / 70


def month_end(year: int, month: int) -> date:
    return date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)


def walk_forward(name, candidates, events, series_map, times_map, start_data, selective):
    folds = []
    all_oos = []
    months = [(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]
    for year, month in months:
        test_start = date(year, month, 1)
        test_end = month_end(year, month)
        train_end = test_start - timedelta(days=1)
        ranked = []
        for candidate in candidates:
            training, _ = evaluate(events, candidate, series_map, times_map, start_data, train_end)
            score = rank_score(training, selective)
            if score > -1e8:
                ranked.append((score, candidate, training))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked:
            folds.append({"month": test_start.strftime("%Y-%m"), "status": "NO_TRAINING_CANDIDATE"})
            continue
        _, candidate, training = ranked[0]
        testing, trades = evaluate(events, candidate, series_map, times_map, test_start, test_end)
        all_oos.extend(trades)
        folds.append({
            "month": test_start.strftime("%Y-%m"),
            "candidate_frozen_before_month": True,
            "candidate": asdict(candidate),
            "training": asdict(training),
            "test": asdict(testing),
            "trades": [
                {**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat(), "entry_time": t.entry_time.isoformat()}
                for t in trades
            ],
        })
    aggregate = stats_for(all_oos)
    positive_months = sum(1 for f in folds if "test" in f and f["test"]["expectancy"] > 0)
    sixty_months = sum(1 for f in folds if "test" in f and f["test"]["win_rate"] >= 60)
    active_months = sum(1 for f in folds if "test" in f and f["test"]["trades"] > 0)
    avg_trades_per_active_month = aggregate.trades / active_months if active_months else 0.0
    if selective:
        passed = (
            18 <= aggregate.trades <= 36
            and aggregate.win_rate >= 70
            and aggregate.expectancy >= 1.5
            and aggregate.profit_factor >= 1.5
            and positive_months >= 5
            and sixty_months >= 5
            and avg_trades_per_active_month <= 4.0
        )
    else:
        passed = (
            aggregate.trades >= 35
            and aggregate.win_rate >= 70
            and aggregate.expectancy > 0
            and aggregate.profit_factor >= 1.2
            and positive_months >= 5
            and sixty_months >= 5
        )
    return {
        "name": name,
        "status": "PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN",
        "candidate_count": len(candidates),
        "aggregate_oos": asdict(aggregate),
        "positive_months": positive_months,
        "sixty_plus_months": sixty_months,
        "active_months": active_months,
        "avg_trades_per_active_month": round(avg_trades_per_active_month, 2),
        "folds": folds,
    }


def main():
    path = _download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx"))
    spot = _parse_spot_frame(path)
    options = _parse_option_frame(path)
    start_data, end_data = date(2025,7,1), date(2026,6,30)
    spot = spot[(spot.timestamp.dt.date >= start_data) & (spot.timestamp.dt.date <= end_data)]
    options = options[(options.day >= start_data) & (options.day <= end_data)]
    spot_by_day = {
        d: tuple(_row_to_candle(r) for r in group.itertuples(index=False))
        for d, group in spot.groupby(spot.timestamp.dt.date, sort=True)
    }
    option_rows_by_day = defaultdict(list)
    for row in options.itertuples(index=False):
        option_rows_by_day[row.day].append(row)
    events, series_map, times_map = build_events(spot_by_day, option_rows_by_day)

    accuracy = walk_forward("Strategy 70 Accuracy", accuracy_grid(), events, series_map, times_map, start_data, False)
    selective = walk_forward("Strategy 70 Selective Profit", selective_grid(), events, series_map, times_map, start_data, True)
    payload = {
        "search_name": "Shiv Dual 70 Monthly Walk-Forward",
        "events_built": len(events),
        "accuracy_proof_rule": ">=35 aggregate OOS trades, >=70% wins, positive expectancy, PF>=1.2, >=5 positive months, >=5 months >=60%.",
        "selective_proof_rule": "18-36 aggregate OOS trades, >=70% wins, expectancy>=1.5 option points/trade, PF>=1.5, <=4 trades per active month, >=5 positive months, >=5 months >=60%.",
        "accuracy": accuracy,
        "selective_profit": selective,
        "overall_status": "BOTH_PROVEN" if accuracy["status"] == "PROVEN_70_PLUS" and selective["status"] == "PROVEN_70_PLUS" else "NOT_BOTH_PROVEN",
    }
    Path("strategy_70_dual.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
