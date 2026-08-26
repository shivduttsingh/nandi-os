from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from strategy_b.public_backtest import run_public_strategy_b_backtest


PROFILES = {
    "BALANCED": {"threshold": 82.0, "cooldown_minutes": 10},
    "STRICT": {"threshold": 88.0, "cooldown_minutes": 15},
    "ELITE": {"threshold": 92.0, "cooldown_minutes": 20},
}


def profile_quality(summary: dict[str, object]) -> float:
    trades = int(summary["trades"])
    expectancy = float(summary["expectancy_points_per_trade"])
    profit_factor = float(summary["profit_factor"])
    win_rate = float(summary["target_win_rate_pct"])
    # Reward positive expectancy and profit factor, while penalizing tiny samples.
    sample_weight = min(1.0, trades / 10.0)
    return sample_weight * (expectancy * 10.0 + min(profit_factor, 4.0) * 3.0 + win_rate / 20.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="strategy_b_backtest_results.json")
    args = parser.parse_args()

    dev_start, dev_end = date(2026, 6, 1), date(2026, 6, 15)
    val_start, val_end = date(2026, 6, 16), date(2026, 6, 30)

    development: dict[str, dict[str, object]] = {}
    for name, params in PROFILES.items():
        report = run_public_strategy_b_backtest(
            dev_start,
            dev_end,
            threshold=params["threshold"],
            cooldown_minutes=params["cooldown_minutes"],
        )
        development[name] = report.as_summary()

    eligible = [name for name, summary in development.items() if int(summary["trades"]) >= 5]
    if not eligible:
        eligible = list(PROFILES)
    chosen = max(eligible, key=lambda name: profile_quality(development[name]))
    chosen_params = PROFILES[chosen]

    validation_report = run_public_strategy_b_backtest(
        val_start,
        val_end,
        threshold=chosen_params["threshold"],
        cooldown_minutes=chosen_params["cooldown_minutes"],
    )
    full_report = run_public_strategy_b_backtest(
        date(2026, 6, 1),
        date(2026, 6, 30),
        threshold=chosen_params["threshold"],
        cooldown_minutes=chosen_params["cooldown_minutes"],
    )

    payload = {
        "methodology": {
            "development_window": "2026-06-01 to 2026-06-15",
            "validation_window": "2026-06-16 to 2026-06-30",
            "selection_rule": "Choose profile on development data only using positive expectancy, profit factor, target win rate and a small-sample penalty. Validation remains unseen during selection.",
            "profiles": PROFILES,
            "target_stop": "+10/-5 option premium points",
            "execution": "Signal at completed 1m candle; buy chosen ATM CE/PE at next 1m candle open plus 0.25-point entry slippage; 0.50 additional points round-trip friction; 15-minute max hold; same-candle target/stop ambiguity counts as stop.",
        },
        "development": development,
        "chosen_profile": chosen,
        "validation": validation_report.as_summary(),
        "full_month": full_report.as_summary(),
        "validation_trade_log": [
            {
                "signal_time": trade.signal_time.isoformat(),
                "entry_time": trade.entry_time.isoformat(),
                "direction": trade.direction,
                "strike": trade.strike,
                "score": trade.score,
                "entry_premium": trade.entry_premium,
                "exit_premium": trade.exit_premium,
                "outcome": trade.outcome,
                "net_points": trade.net_points,
                "hold_minutes": trade.hold_minutes,
            }
            for trade in validation_report.trades
        ],
    }

    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
