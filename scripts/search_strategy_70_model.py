from __future__ import annotations

import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

import numpy as np

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
    features: tuple[float, ...]
    accuracy_outcome: str
    accuracy_net: float
    selective_outcome: str
    selective_net: float


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: datetime
    direction: str
    strike: int
    probability: float
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


def atr(candles, lookback: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    previous = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    values = []
    for candle in sample:
        values.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return mean(values) if values else 0.0


def move(candles, lookback: int) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def oi_change(candles, lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    return pct(old, candles[-1].open_interest) if old > 0 else 0.0


def volume_ratio(candles, lookback: int = 12) -> float:
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def outcome_from_next_open(series, times, signal_time, target, stop, hold):
    index = bisect_right(times, signal_time)
    if index >= len(series) or series[index].timestamp.date() != signal_time.date():
        return "TIMEOUT", -0.50
    entry = series[index].open + 0.20
    stop_price = max(0.05, entry - stop)
    target_price = entry + target
    cutoff = series[index].timestamp + timedelta(minutes=hold)
    future = [c for c in series[index:] if c.timestamp.date() == signal_time.date() and c.timestamp <= cutoff]
    if not future:
        return "TIMEOUT", -0.50
    for candle in future:
        if candle.low <= stop_price:
            return "LOSS", -stop - 0.50
        if candle.high >= target_price:
            return "WIN", target - 0.50
    return "TIMEOUT", future[-1].close - entry - 0.50


def build_events(spot_by_day, option_rows_by_day):
    events = []
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

        full_series = {k: tuple(sorted(v, key=lambda c: c.timestamp)) for k, v in option_lists.items()}
        full_times = {k: [c.timestamp for c in v] for k, v in full_series.items()}
        history = defaultdict(list)
        n1 = []
        n5 = []
        n15 = []

        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            _update_aggregate(n15, spot, 15)
            for side, row in at_time.get(spot.timestamp, []):
                history[(side, int(row.strike))].append(_row_to_candle(row))

            if not (time(9, 35) <= spot.timestamp.time() <= time(13, 15)):
                continue
            if len(n1) < 25 or len(n5) < 5 or len(n15) < 3:
                continue
            spot_atr = atr(n1[-20:])
            if spot_atr <= 0:
                continue
            strike = _nearest_common_strike(strikes, spot.close)
            if strike is None:
                continue

            closes5 = [c.close for c in n5[-12:]]
            trend_gap_raw = ema(closes5, 5) - ema(closes5, 9)
            fifteen_raw = n15[-2].close - n15[-3].close
            prior5_high = max(c.high for c in n1[-6:-1])
            prior5_low = min(c.low for c in n1[-6:-1])
            spot_range_atr = (spot.high - spot.low) / spot_atr
            minute_norm = ((spot.timestamp.hour * 60 + spot.timestamp.minute) - (9 * 60 + 15)) / 225.0

            for direction, sign in (("CE", 1.0), ("PE", -1.0)):
                chosen = history.get((direction, strike), [])
                opposite = history.get(("PE" if direction == "CE" else "CE", strike), [])
                if len(chosen) < 15 or len(opposite) < 6:
                    continue
                if chosen[-1].close < 20:
                    continue
                opt_closes = [c.close for c in chosen[-20:]]
                opt_fast = ema(opt_closes, 5)
                opt_slow = ema(opt_closes, 13)
                option_range = max(chosen[-1].high - chosen[-1].low, 1e-9)
                breakout_distance = (
                    (spot.close - prior5_high) / spot_atr if direction == "CE"
                    else (prior5_low - spot.close) / spot_atr
                )
                features = (
                    sign * (spot.close - n1[-2].close) / spot_atr,
                    sign * (spot.close - n1[-4].close) / spot_atr,
                    sign * (spot.close - n1[-6].close) / spot_atr,
                    sign * trend_gap_raw / spot_atr,
                    sign * fifteen_raw / spot_atr,
                    breakout_distance,
                    spot_range_atr,
                    move(chosen, 1),
                    move(chosen, 3),
                    move(chosen, 5),
                    move(chosen, 3) - move(opposite, 3),
                    move(chosen, 1) - move(opposite, 1),
                    volume_ratio(chosen),
                    oi_change(chosen, 1),
                    oi_change(chosen, 3),
                    oi_change(chosen, 3) - oi_change(opposite, 3),
                    pct(max(opt_slow, 0.01), chosen[-1].close),
                    pct(max(opt_slow, 0.01), opt_fast),
                    (chosen[-1].close - chosen[-1].open) / option_range,
                    (chosen[-1].high - chosen[-1].low) / max(chosen[-1].close, 0.01) * 100.0,
                    math.log(max(chosen[-1].close, 1.0)),
                    minute_norm,
                    1.0 if direction == "CE" else -1.0,
                )
                key = (direction, strike)
                acc_outcome, acc_net = outcome_from_next_open(full_series[key], full_times[key], spot.timestamp, 4.0, 5.0, 20)
                sel_outcome, sel_net = outcome_from_next_open(full_series[key], full_times[key], spot.timestamp, 8.0, 5.0, 30)
                events.append(Event(day_value, spot.timestamp, direction, strike, tuple(float(x) for x in features), acc_outcome, round(acc_net, 2), sel_outcome, round(sel_net, 2)))

    events.sort(key=lambda e: (e.signal_time, e.direction))
    return events


def fit_logistic(events, geometry):
    if len(events) < 80:
        return None
    X = np.array([e.features for e in events], dtype=float)
    y = np.array([1.0 if (e.accuracy_outcome if geometry == "accuracy" else e.selective_outcome) == "WIN" else 0.0 for e in events], dtype=float)
    if y.sum() < 10 or (len(y) - y.sum()) < 10:
        return None
    mean_x = X.mean(axis=0)
    std_x = X.std(axis=0)
    std_x[std_x < 1e-6] = 1.0
    Z = (X - mean_x) / std_x
    Z = np.column_stack([np.ones(len(Z)), Z])
    weights = np.zeros(Z.shape[1], dtype=float)
    positives = max(1.0, y.sum())
    negatives = max(1.0, len(y) - y.sum())
    sample_weights = np.where(y > 0.5, len(y) / (2.0 * positives), len(y) / (2.0 * negatives))
    lr = 0.035
    l2 = 0.35
    for _ in range(450):
        logits = np.clip(Z @ weights, -25.0, 25.0)
        probs = 1.0 / (1.0 + np.exp(-logits))
        error = (probs - y) * sample_weights
        grad = (Z.T @ error) / len(y)
        grad[1:] += l2 * weights[1:] / len(y)
        weights -= lr * grad
    return mean_x, std_x, weights


def predict(model, events):
    if model is None or not events:
        return np.array([], dtype=float)
    mean_x, std_x, weights = model
    X = np.array([e.features for e in events], dtype=float)
    Z = (X - mean_x) / std_x
    Z = np.column_stack([np.ones(len(Z)), Z])
    logits = np.clip(Z @ weights, -25.0, 25.0)
    return 1.0 / (1.0 + np.exp(-logits))


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
    equity = peak = dd = 0.0
    for trade in trades:
        equity += trade.net_points
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return Stats(n, wins, losses, timeouts, round(100*wins/n, 2) if n else 0.0, round(net, 2), round(net/n, 2) if n else 0.0, round(pf, 2), round(dd, 2))


def choose_trades(events, probs, threshold, margin, geometry):
    grouped = defaultdict(lambda: defaultdict(list))
    for event, prob in zip(events, probs):
        grouped[event.day][event.signal_time].append((float(prob), event))
    trades = []
    for day_value in sorted(grouped):
        selected = None
        for signal_time in sorted(grouped[day_value]):
            ranked = sorted(grouped[day_value][signal_time], key=lambda x: x[0], reverse=True)
            best_prob, best_event = ranked[0]
            other_prob = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_prob >= threshold and best_prob - other_prob >= margin:
                selected = (best_prob, best_event)
                break
        if selected is not None:
            prob, event = selected
            outcome = event.accuracy_outcome if geometry == "accuracy" else event.selective_outcome
            net = event.accuracy_net if geometry == "accuracy" else event.selective_net
            trades.append(Trade(event.day, event.signal_time, event.direction, event.strike, round(prob, 4), outcome, net))
    return tuple(trades)


def calibrate(model, events, geometry):
    probs = predict(model, events)
    if geometry == "accuracy":
        thresholds = (0.52, 0.56, 0.60, 0.64, 0.68, 0.72, 0.76, 0.80, 0.84)
        margins = (0.00, 0.04, 0.08, 0.12)
        min_trades = 4
    else:
        thresholds = (0.62, 0.68, 0.74, 0.80, 0.86, 0.90, 0.93)
        margins = (0.04, 0.08, 0.12, 0.16)
        min_trades = 2
    best = None
    for threshold in thresholds:
        for margin in margins:
            trades = choose_trades(events, probs, threshold, margin, geometry)
            stats = stats_for(trades)
            if stats.trades < min_trades or stats.expectancy <= 0 or stats.profit_factor <= 1.0:
                continue
            score = stats.win_rate + min(stats.profit_factor, 4.0) * 6 + min(stats.expectancy, 5.0) * (5 if geometry == "selective" else 2)
            if geometry == "selective":
                score -= max(0, stats.trades - 5) * 2
            if best is None or score > best[0]:
                best = (score, threshold, margin, stats)
    return best


def month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def run_geometry(events, geometry):
    folds = []
    all_oos = []
    test_months = [(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]
    for year, month in test_months:
        test_start, test_end = month_bounds(year, month)
        calibration_end = test_start - timedelta(days=1)
        calibration_start = date(calibration_end.year, calibration_end.month, 1)
        fit_end = calibration_start - timedelta(days=1)
        fit_events = [e for e in events if date(2025,7,1) <= e.day <= fit_end]
        calibration_events = [e for e in events if calibration_start <= e.day <= calibration_end]
        test_events = [e for e in events if test_start <= e.day <= test_end]
        model = fit_logistic(fit_events, geometry)
        calibration = calibrate(model, calibration_events, geometry) if model is not None else None
        if calibration is None:
            folds.append({"month": test_start.strftime("%Y-%m"), "status": "NO_CALIBRATED_MODEL"})
            continue
        _, threshold, margin, cal_stats = calibration
        test_probs = predict(model, test_events)
        trades = choose_trades(test_events, test_probs, threshold, margin, geometry)
        test_stats = stats_for(trades)
        all_oos.extend(trades)
        folds.append({
            "month": test_start.strftime("%Y-%m"),
            "model_fit_through": fit_end.isoformat(),
            "calibration_month": calibration_start.strftime("%Y-%m"),
            "threshold": threshold,
            "probability_margin": margin,
            "calibration": asdict(cal_stats),
            "test": asdict(test_stats),
            "trades": [
                {**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat()}
                for t in trades
            ],
        })
    aggregate = stats_for(all_oos)
    positive_months = sum(1 for f in folds if "test" in f and f["test"]["expectancy"] > 0)
    sixty_months = sum(1 for f in folds if "test" in f and f["test"]["win_rate"] >= 60)
    active_months = sum(1 for f in folds if "test" in f and f["test"]["trades"] > 0)
    avg = aggregate.trades / active_months if active_months else 0.0
    if geometry == "accuracy":
        passed = aggregate.trades >= 30 and aggregate.win_rate >= 70 and aggregate.expectancy > 0 and aggregate.profit_factor >= 1.2 and positive_months >= 4 and sixty_months >= 4
    else:
        passed = 14 <= aggregate.trades <= 28 and aggregate.win_rate >= 70 and aggregate.expectancy >= 1.5 and aggregate.profit_factor >= 1.5 and positive_months >= 4 and sixty_months >= 4 and avg <= 4.0
    return {
        "geometry": geometry,
        "status": "PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN",
        "aggregate_oos": asdict(aggregate),
        "positive_months": positive_months,
        "sixty_plus_months": sixty_months,
        "active_months": active_months,
        "avg_trades_per_active_month": round(avg, 2),
        "folds": folds,
    }


def main():
    path = _download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx"))
    spot = _parse_spot_frame(path)
    options = _parse_option_frame(path)
    lo, hi = date(2025,7,1), date(2026,6,30)
    spot = spot[(spot.timestamp.dt.date >= lo) & (spot.timestamp.dt.date <= hi)]
    options = options[(options.day >= lo) & (options.day <= hi)]
    spot_by_day = {d: tuple(_row_to_candle(r) for r in group.itertuples(index=False)) for d, group in spot.groupby(spot.timestamp.dt.date, sort=True)}
    rows = defaultdict(list)
    for row in options.itertuples(index=False):
        rows[row.day].append(row)
    events = build_events(spot_by_day, rows)
    accuracy = run_geometry(events, "accuracy")
    selective = run_geometry(events, "selective")
    payload = {
        "search_name": "Shiv Leakage-Controlled Logistic Monthly Walk-Forward",
        "events_built": len(events),
        "method": "For each test month, fit the model only through the month before calibration; select probability threshold on the immediately preceding calibration month; then freeze both model and threshold for the next month. One trade maximum per day.",
        "accuracy_target_stop": "+4/-5 option points, 20 minute max hold, 0.20 entry slippage + 0.50 friction",
        "selective_target_stop": "+8/-5 option points, 30 minute max hold, 0.20 entry slippage + 0.50 friction",
        "accuracy": accuracy,
        "selective_profit": selective,
        "overall_status": "BOTH_PROVEN" if accuracy["status"] == "PROVEN_70_PLUS" and selective["status"] == "PROVEN_70_PLUS" else "NOT_BOTH_PROVEN",
    }
    Path("strategy_70_model.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
