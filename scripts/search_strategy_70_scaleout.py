from __future__ import annotations

import importlib.util
import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("option_impulse_base", ROOT / "scripts" / "search_strategy_70_option_impulse.py")
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = base
spec.loader.exec_module(base)


@dataclass(frozen=True)
class Candidate:
    min_spot_gap_atr: float
    min_spot_move_atr: float
    min_option_move_pct: float
    min_outperformance_pct: float
    min_volume_ratio: float
    require_option_ema: bool
    min_premium: float
    stop_points: float
    first_target: float
    runner_target: float
    first_fraction: float


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: object
    direction: str
    strike: int
    outcome: str
    net_points: float
    first_target_hit: bool
    runner_target_hit: bool


def qualifies(event, candidate):
    return (
        event.spot_trend_gap_atr >= candidate.min_spot_gap_atr
        and event.spot_move_atr >= candidate.min_spot_move_atr
        and event.option_move_pct >= candidate.min_option_move_pct
        and event.option_outperformance_pct >= candidate.min_outperformance_pct
        and event.option_volume_ratio >= candidate.min_volume_ratio
        and (not candidate.require_option_ema or event.option_ema_aligned)
        and event.option_premium >= candidate.min_premium
        and event.option_oi_change_pct >= -12.0
    )


def simulate(event, candidate, series, times):
    start = bisect_right(times, event.signal_time)
    trigger = event.option_high + 0.10
    deadline = event.signal_time + timedelta(minutes=1)
    entry_index = -1
    entry = 0.0
    for index in range(start, len(series)):
        candle = series[index]
        if candle.timestamp.date() != event.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            entry_index = index
            entry = max(trigger, candle.open) + 0.20
            break
    if entry_index < 0:
        return None

    stop = max(0.05, entry - candidate.stop_points)
    first = entry + candidate.first_target
    runner = entry + candidate.runner_target
    cutoff = series[entry_index].timestamp + timedelta(minutes=30)
    future = [c for c in series[entry_index:] if c.timestamp.date() == event.day and c.timestamp <= cutoff]
    if not future:
        return None

    first_hit = False
    runner_hit = False
    runner_exit = None
    for candle in future:
        if not first_hit:
            stop_hit = candle.low <= stop
            first_hit_now = candle.high >= first
            if stop_hit:
                net = -candidate.stop_points - 0.50
                return Trade(event.day, event.signal_time, event.direction, event.strike, "LOSS", round(net, 2), False, False)
            if first_hit_now:
                first_hit = True
                # Once first target is booked, runner stop moves to breakeven.
                # If breakeven and runner target both appear in the same 1m candle, take breakeven conservatively.
                if candle.low <= entry:
                    runner_exit = 0.0
                    break
                if candle.high >= runner:
                    runner_hit = True
                    runner_exit = candidate.runner_target
                    break
        else:
            if candle.low <= entry:
                runner_exit = 0.0
                break
            if candle.high >= runner:
                runner_hit = True
                runner_exit = candidate.runner_target
                break

    if not first_hit:
        last_move = future[-1].close - entry
        net = last_move - 0.50
    else:
        if runner_exit is None:
            runner_exit = max(0.0, future[-1].close - entry)
        gross = candidate.first_fraction * candidate.first_target + (1.0 - candidate.first_fraction) * runner_exit
        net = gross - 0.50
    outcome = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
    return Trade(event.day, event.signal_time, event.direction, event.strike, outcome, round(net, 2), first_hit, runner_hit)


def stats_for(trades):
    trades = tuple(trades)
    total = len(trades)
    wins = sum(t.net_points > 0 for t in trades)
    losses = sum(t.net_points < 0 for t in trades)
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    equity = peak = drawdown = 0.0
    for trade in trades:
        equity += trade.net_points
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    first_hits = sum(t.first_target_hit for t in trades)
    runner_hits = sum(t.runner_target_hit for t in trades)
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100.0 * wins / total, 2) if total else 0.0,
        "net_points": round(net, 2),
        "expectancy": round(net / total, 2) if total else 0.0,
        "profit_factor": round(pf, 2),
        "max_drawdown": round(drawdown, 2),
        "first_target_hit_rate": round(100.0 * first_hits / total, 2) if total else 0.0,
        "runner_hit_rate": round(100.0 * runner_hits / total, 2) if total else 0.0,
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


def grid():
    candidates = []
    for values in itertools.product(
        (0.08, 0.15),
        (0.25, 0.50),
        (0.7, 1.5),
        (0.8, 1.5),
        (1.1, 1.5),
        (True,),
        (30.0, 50.0),
        (4.0, 5.0),
        (3.0, 4.0),
        (8.0, 10.0),
        (0.50, 0.65, 0.75),
    ):
        candidates.append(Candidate(*values))
    return candidates


def rank_score(stats):
    if stats["trades"] < 25 or stats["expectancy"] <= 0 or stats["profit_factor"] <= 1.10:
        return -1e9
    p = stats["wins"] / stats["trades"]
    z = 1.0
    lower = (p + z*z/(2*stats["trades"]) - z*math.sqrt((p*(1-p)+z*z/(4*stats["trades"]))/stats["trades"])) / (1 + z*z/stats["trades"])
    return lower * 100 + min(stats["profit_factor"], 4.0) * 5 + min(stats["expectancy"], 4.0) * 4 - stats["max_drawdown"] / 60


def month_end(year, month):
    return date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)


def main():
    path = base._download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx"))
    spot_df = base._parse_spot_frame(path)
    opt_df = base._parse_option_frame(path)
    lo, hi = date(2025, 7, 1), date(2026, 6, 30)
    spot_df = spot_df[(spot_df["timestamp"].dt.date >= lo) & (spot_df["timestamp"].dt.date <= hi)]
    opt_df = opt_df[(opt_df["day"] >= lo) & (opt_df["day"] <= hi)]
    spot_by_day = {
        d: tuple(base._row_to_candle(row) for row in group.itertuples(index=False))
        for d, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True)
    }
    option_rows = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows[row.day].append(row)
    events, series_map, times_map = base.build_events(spot_by_day, option_rows)
    candidates = grid()

    folds = []
    all_oos = []
    for year, month in ((2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)):
        test_start = date(year, month, 1)
        test_end = month_end(year, month)
        train_end = test_start - timedelta(days=1)
        ranked = []
        for candidate in candidates:
            training, _ = evaluate(events, candidate, series_map, times_map, lo, train_end)
            score = rank_score(training)
            if score > -1e8:
                ranked.append((score, candidate, training))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            folds.append({"month": test_start.strftime("%Y-%m"), "status": "NO_TRAINING_CANDIDATE"})
            continue
        _, chosen, training = ranked[0]
        testing, trades = evaluate(events, chosen, series_map, times_map, test_start, test_end)
        all_oos.extend(trades)
        folds.append({
            "month": test_start.strftime("%Y-%m"),
            "candidate_frozen_before_month": True,
            "candidate": asdict(chosen),
            "training": training,
            "test": testing,
            "trades": [
                {**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat()}
                for t in trades
            ],
        })

    aggregate = stats_for(all_oos)
    positive_months = sum(1 for fold in folds if "test" in fold and fold["test"]["expectancy"] > 0)
    sixty_months = sum(1 for fold in folds if "test" in fold and fold["test"]["win_rate"] >= 60)
    passed = (
        aggregate["trades"] >= 30
        and aggregate["win_rate"] >= 70
        and aggregate["expectancy"] >= 0.50
        and aggregate["profit_factor"] >= 1.30
        and positive_months >= 5
        and sixty_months >= 5
    )
    payload = {
        "search_name": "Shiv Scale-Out Option Impulse Monthly Walk-Forward",
        "candidate_count": len(candidates),
        "events_built": len(events),
        "status": "PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN",
        "proof_rule": ">=30 aggregate OOS trades, >=70% positive-P&L trades, expectancy>=0.50 weighted option points/trade after friction, PF>=1.30, >=5 positive months and >=5 months >=60% wins.",
        "execution_note": "One trade/day. Initial stop 4-5 option points. Book 50-75% at +3/+4, move runner stop to breakeven, runner target +8/+10. Conservative 1m intrabar ordering. Weighted P&L includes 0.50 point friction.",
        "aggregate_oos": aggregate,
        "positive_months": positive_months,
        "sixty_plus_months": sixty_months,
        "folds": folds,
    }
    Path("strategy_70_scaleout.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
