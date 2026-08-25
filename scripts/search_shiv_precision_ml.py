from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts import search_shiv_precision_v1 as base

FEATURES = [
    "impulse", "efficiency", "atm_move", "atm_edge", "weighted_move",
    "dominance", "oi_support", "volume_ratio", "structure15", "structure3",
    "position_ok", "minute_sin", "minute_cos", "side_ce",
]
THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)


def prepare(rows: pd.DataFrame, gate: str) -> pd.DataFrame:
    sample = rows[rows.gate == gate].copy()
    sample = base._dedup(sample, cooldown=20).sort_values("entry_time").copy()
    minute = sample.minute.astype(float)
    phase = 2.0 * np.pi * (minute - 555.0) / 375.0
    sample["minute_sin"] = np.sin(phase)
    sample["minute_cos"] = np.cos(phase)
    sample["side_ce"] = (sample.side == "CE").astype(int)
    for name in ("oi_support", "structure15", "structure3", "position_ok"):
        sample[name] = sample[name].astype(int)
    sample["label"] = (sample.result == "WIN").astype(int)
    return sample


def make_models():
    return {
        "logistic_l2": make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.35, penalty="l2", class_weight=None, max_iter=1000, random_state=7),
        ),
        "histgb_shallow": HistGradientBoostingClassifier(
            learning_rate=0.045,
            max_iter=120,
            max_leaf_nodes=7,
            max_depth=2,
            min_samples_leaf=12,
            l2_regularization=2.5,
            random_state=7,
        ),
    }


def stats(sample: pd.DataFrame):
    n = len(sample)
    wins = int(sample.label.sum()) if n else 0
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": round(100.0 * wins / n, 2) if n else 0.0,
    }


def max_losing_streak(sample: pd.DataFrame) -> int:
    streak = best = 0
    for label in sample.sort_values("entry_time").label.tolist():
        if label:
            streak = 0
        else:
            streak += 1
            best = max(best, streak)
    return best


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="shiv_precision_ml.json")
    p.add_argument("--cache", default=".cache/shiv-precision")
    args = p.parse_args()

    rows, loaded, tested, skipped = base.build_rows(date(2026, 1, 1), date(2026, 6, 30), Path(args.cache))
    candidates = []
    fitted = {}

    # Gate choice and model choice are made only from Jan-Apr. May-Jun remains untouched.
    for gate in ("breakout", "pullback_reclaim"):
        sample = prepare(rows, gate)
        train = sample[sample.month.isin([1, 2])].copy()
        val = sample[sample.month.isin([3, 4])].copy()
        if len(train) < 40 or len(val) < 30 or train.label.nunique() < 2:
            continue
        Xtr, ytr = train[FEATURES], train.label
        Xv = val[FEATURES]
        for model_name, model in make_models().items():
            model.fit(Xtr, ytr)
            p_train = model.predict_proba(Xtr)[:, 1]
            p_val = model.predict_proba(Xv)[:, 1]
            for threshold in THRESHOLDS:
                tr_sel = train[p_train >= threshold]
                va_sel = val[p_val >= threshold]
                tr_stats = stats(tr_sel)
                va_stats = stats(va_sel)
                if tr_stats["trades"] < 15 or va_stats["trades"] < 15:
                    continue
                robust = min(tr_stats["win_rate_pct"], va_stats["win_rate_pct"])
                candidates.append({
                    "gate": gate,
                    "model": model_name,
                    "threshold": threshold,
                    "train": tr_stats,
                    "validation": va_stats,
                    "robust_rate": robust,
                })
                fitted[(gate, model_name)] = model

    candidates.sort(
        key=lambda x: (x["robust_rate"], x["validation"]["trades"], x["validation"]["win_rate_pct"]),
        reverse=True,
    )
    best = candidates[0] if candidates else None
    payload = {
        "strategy": "SHIV PRECISION CLASSIFIER research",
        "protocol": "Jan-Feb train; Mar-Apr choose gate/model/probability threshold; selected rule locked before May-Jun final test.",
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop counts as LOSS",
        "features": FEATURES,
        "candidate_count": len(candidates),
        "best_pre_final": best,
        "top_10_pre_final": candidates[:10],
        "tested_days": len(tested),
        "loaded_expiry_files": loaded,
        "skipped_days": skipped,
    }

    if best:
        gate = best["gate"]
        sample = prepare(rows, gate)
        development = sample[sample.month.isin([1, 2, 3, 4])].copy()
        final = sample[sample.month.isin([5, 6])].copy()
        model = make_models()[best["model"]]
        model.fit(development[FEATURES], development.label)
        final_prob = model.predict_proba(final[FEATURES])[:, 1]
        selected = final[final_prob >= best["threshold"]].copy()
        selected["model_probability"] = final_prob[final_prob >= best["threshold"]]
        s = stats(selected)
        s["max_losing_streak"] = max_losing_streak(selected)
        s["median_mfe"] = round(float(selected.mfe.median()), 2) if len(selected) else 0.0
        s["median_mae"] = round(float(selected.mae.median()), 2) if len(selected) else 0.0
        s["signals"] = [
            {
                "entry_time": r.entry_time.isoformat(),
                "side": r.side,
                "gate": r.gate,
                "model_probability": round(float(r.model_probability), 4),
                "result": r.result,
                "mfe": round(float(r.mfe), 2),
                "mae": round(float(r.mae), 2),
            }
            for r in selected.itertuples()
        ]
        payload["may_june_final_test"] = s
        payload["accept_for_paper_live"] = bool(s["trades"] >= 20 and s["win_rate_pct"] >= 70.0 and s["max_losing_streak"] <= 3)
        payload["target_band_met"] = bool(s["trades"] >= 20 and 70.0 <= s["win_rate_pct"] <= 90.0)
    else:
        payload["may_june_final_test"] = {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0}
        payload["accept_for_paper_live"] = False
        payload["target_band_met"] = False

    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
