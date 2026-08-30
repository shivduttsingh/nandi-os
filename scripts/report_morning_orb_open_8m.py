from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import scripts.search_morning_orb_open_8m as base
from nandi_v2.profit_first import metrics


def exploratory_score(stats: dict) -> float:
    trades = int(stats.get("trades") or 0)
    if trades < 8:
        return -1e12
    wr = float(stats.get("win_rate") or 0.0)
    pf = float(stats.get("profit_factor") or 0.0)
    exp = float(stats.get("expectancy") or -999.0)
    net = float(stats.get("net_points") or 0.0)
    return wr * 2.0 + min(pf, 3.0) * 15.0 + exp * 8.0 + net * 0.1 + min(trades, 30) * 0.25


def main() -> None:
    contexts = base.build_contexts()
    ranked = []
    for candidate in base.grid():
        stats, _ = base.evaluate(contexts, candidate, base.START, base.TRAIN_END)
        ranked.append((exploratory_score(stats), candidate, stats))
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, chosen, train = ranked[0]

    train, train_trades = base.evaluate(contexts, chosen, base.START, base.TRAIN_END)
    valid, valid_trades = base.evaluate(contexts, chosen, base.VALID_START, base.VALID_END)
    stress, stress_trades = base.evaluate(contexts, chosen, base.STRESS_START, base.STRESS_END)
    all_trades = pd.concat([train_trades, valid_trades, stress_trades], ignore_index=True)

    strict_pass = (
        valid["trades"] >= 20
        and float(valid["win_rate"] or 0) >= 70
        and float(valid["profit_factor"] or 0) >= 1.5
        and float(valid["expectancy"] or 0) > 0
        and float(valid["net_points"] or 0) > 0
        and stress["trades"] >= 15
        and float(stress["win_rate"] or 0) >= 60
        and float(stress["profit_factor"] or 0) >= 1.2
        and float(stress["expectancy"] or 0) > 0
        and float(stress["net_points"] or 0) > 0
    )

    payload = {
        "search_name": "Morning ORB V2 Open Data Full 8M Exploratory",
        "data_source": base.PUBLIC_SAMPLE_PROJECT,
        "license": base.PUBLIC_SAMPLE_LICENSE,
        "window": [base.START.isoformat(), base.END.isoformat()],
        "selection_window": [base.START.isoformat(), base.TRAIN_END.isoformat()],
        "validation_window": [base.VALID_START.isoformat(), base.VALID_END.isoformat()],
        "stress_window": [base.STRESS_START.isoformat(), base.STRESS_END.isoformat()],
        "chosen": asdict(chosen),
        "training": train,
        "validation": valid,
        "stress": stress,
        "all_8m": metrics(all_trades),
        "monthly": base.monthly(all_trades),
        "strict_pass": strict_pass,
        "status": "PASS_TO_UPSTOX" if strict_pass else "REJECT_OR_REFINE",
        "warning": "Exploratory ranking allowed a small training sample only to expose the full 8-month behavior. It is not proof and does not override the strict gate.",
    }
    Path("morning_orb_open_8m_full.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    all_trades.to_csv("morning_orb_open_8m_full_trades.csv", index=False)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
