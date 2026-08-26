from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_b.v2_backtest import PROFILES, run_v2_backtest


def quality(summary: dict[str, object]) -> float:
    trades = int(summary["trades"])
    expectancy = float(summary["expectancy_points_per_trade"])
    profit_factor = float(summary["profit_factor"])
    win_rate = float(summary["target_win_rate_pct"])
    drawdown = float(summary["max_drawdown_points"])
    sample_weight = min(1.0, trades / 12.0)
    return sample_weight * (expectancy * 12.0 + min(profit_factor, 4.0) * 4.0 + win_rate / 15.0 - drawdown / 100.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="strategy_b_v2_backtest_results.json")
    args = parser.parse_args()

    development_window = (date(2026, 4, 1), date(2026, 4, 30))
    validation_window = (date(2026, 5, 1), date(2026, 5, 31))
    stress_window = (date(2026, 6, 1), date(2026, 6, 30))

    development: dict[str, dict[str, object]] = {}
    for profile in PROFILES:
        development[profile.name] = run_v2_backtest(*development_window, profile).summary()

    eligible = [
        profile for profile in PROFILES
        if int(development[profile.name]["trades"]) >= 8
        and float(development[profile.name]["expectancy_points_per_trade"]) > 0
        and float(development[profile.name]["profit_factor"]) > 1.0
    ]
    if not eligible:
        eligible = [profile for profile in PROFILES if int(development[profile.name]["trades"]) >= 5]
    if not eligible:
        eligible = list(PROFILES)

    chosen = max(eligible, key=lambda profile: quality(development[profile.name]))
    validation = run_v2_backtest(*validation_window, chosen)
    stress = run_v2_backtest(*stress_window, chosen)

    payload = {
        "strategy": "Strategy B v2 selective breakout trigger",
        "methodology": {
            "development_window": "2026-04-01 to 2026-04-30",
            "untouched_validation_window": "2026-05-01 to 2026-05-31",
            "post_selection_stress_window": "2026-06-01 to 2026-06-30",
            "validation_used_for_selection": False,
            "stress_used_for_selection": False,
            "selection_rule": "Select only on April development results. Prefer >=8 trades with positive expectancy and PF>1; score expectancy, PF, win rate, drawdown and sample size.",
            "execution": "After a completed high-quality signal, arm a buy-stop above the chosen ATM option signal candle high for 2-3 minutes. Risk is max(minimum points, percentage of premium, option ATR multiple), rejected if too wide. Target is 1.8-2.0R. Same-candle target/stop ambiguity is a loss. Max 2-3 trades/day.",
        },
        "development": development,
        "chosen_profile": chosen.name,
        "chosen_parameters": chosen.__dict__,
        "validation": validation.summary(),
        "stress_june": stress.summary(),
        "validation_trades": [
            {
                "signal_time": t.signal_time.isoformat(),
                "entry_time": t.entry_time.isoformat(),
                "direction": t.direction,
                "strike": t.strike,
                "score": t.score,
                "entry": t.entry,
                "stop_distance": t.stop_distance,
                "target_distance": t.target_distance,
                "outcome": t.outcome,
                "net_points": t.net_points,
                "hold_minutes": t.hold_minutes,
            }
            for t in validation.trades
        ],
    }

    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
