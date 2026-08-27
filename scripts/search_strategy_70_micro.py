from __future__ import annotations

import importlib.util
import itertools
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict
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


def grid():
    candidates = []
    exits = ((3.0, 4.0), (4.0, 5.0))
    for gap, spot_move, opt_move, outperf, volume, require_ema, premium, pair in itertools.product(
        (0.05, 0.12, 0.20),
        (0.20, 0.45, 0.70),
        (0.5, 1.5, 2.5),
        (0.5, 1.2, 2.0),
        (1.0, 1.4, 1.8),
        (False, True),
        (30.0, 50.0, 70.0),
        exits,
    ):
        candidates.append(base.Candidate(gap, spot_move, opt_move, outperf, volume, require_ema, premium, pair[0], pair[1]))
    return candidates


def rank_score(stats):
    if stats.trades < 40 or stats.expectancy <= 0 or stats.profit_factor <= 1.05:
        return -1e9
    p = stats.wins / stats.trades
    z = 1.0
    lower = (p + z*z/(2*stats.trades) - z*math.sqrt((p*(1-p)+z*z/(4*stats.trades))/stats.trades)) / (1 + z*z/stats.trades)
    return lower * 100 + min(stats.profit_factor, 3.0) * 6 + min(stats.expectancy, 3.0) * 4 - stats.max_drawdown / 60


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
            training, _ = base.evaluate(events, candidate, series_map, times_map, lo, train_end)
            score = rank_score(training)
            if score > -1e8:
                ranked.append((score, candidate, training))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            folds.append({"month": test_start.strftime("%Y-%m"), "status": "NO_TRAINING_CANDIDATE"})
            continue
        _, chosen, training = ranked[0]
        testing, trades = base.evaluate(events, chosen, series_map, times_map, test_start, test_end)
        all_oos.extend(trades)
        folds.append({
            "month": test_start.strftime("%Y-%m"),
            "candidate_frozen_before_month": True,
            "candidate": asdict(chosen),
            "training": asdict(training),
            "test": asdict(testing),
            "trades": [
                {**asdict(t), "day": t.day.isoformat(), "signal_time": t.signal_time.isoformat(), "entry_time": t.entry_time.isoformat()}
                for t in trades
            ],
        })

    aggregate = base.stats_for(all_oos)
    positive_months = sum(1 for fold in folds if "test" in fold and fold["test"]["expectancy"] > 0)
    sixty_months = sum(1 for fold in folds if "test" in fold and fold["test"]["win_rate"] >= 60)
    passed = (
        aggregate.trades >= 40
        and aggregate.win_rate >= 70
        and aggregate.expectancy >= 0.25
        and aggregate.profit_factor >= 1.25
        and positive_months >= 6
        and sixty_months >= 6
    )
    payload = {
        "search_name": "Shiv Micro-Accuracy Option Impulse Monthly Walk-Forward",
        "candidate_count": len(candidates),
        "events_built": len(events),
        "status": "PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN",
        "proof_rule": ">=40 aggregate OOS trades, >=70% target wins, expectancy>=0.25 option points/trade after friction, PF>=1.25, >=6 positive months and >=6 months >=60% wins.",
        "execution_note": "One trade maximum per day; buy-stop above completed ATM option signal candle, next minute only; 0.20 point entry slippage and 0.50 additional friction; exits restricted to +3/-4 or +4/-5 option points.",
        "aggregate_oos": asdict(aggregate),
        "positive_months": positive_months,
        "sixty_plus_months": sixty_months,
        "folds": folds,
    }
    Path("strategy_70_micro.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
