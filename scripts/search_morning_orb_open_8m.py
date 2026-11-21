from __future__ import annotations

import itertools
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nandi_v2.profit_first import metrics
from test1.public_backtest import (
    PUBLIC_SAMPLE_LICENSE,
    PUBLIC_SAMPLE_PROJECT,
    _download_public_sample,
    _parse_option_frame,
    _parse_spot_frame,
)

START = date(2025, 11, 1)
END = date(2026, 6, 30)
TRAIN_END = date(2026, 2, 28)
VALID_START = date(2026, 3, 1)
VALID_END = date(2026, 4, 30)
STRESS_START = date(2026, 5, 1)
STRESS_END = END

OPEN_START = 9 * 60 + 15
OPEN_END = 9 * 60 + 29
ENTRY_START = 9 * 60 + 30
LAST_SIGNAL = 11 * 60 + 44
ENTRY_SLIPPAGE = 0.20
FRICTION = 0.50


@dataclass(frozen=True)
class Candidate:
    min_option_move_pct: float
    min_option_outperformance_pct: float
    min_volume_ratio: float
    min_abs_oi_change_pct: float
    target_points: float
    stop_points: float
    max_hold_minutes: int


def grid() -> list[Candidate]:
    values = itertools.product(
        (0.40, 0.70),
        (0.40, 0.70),
        (1.00, 1.30),
        (0.00, 0.20),
        ((6.0, 5.0), (8.0, 6.0), (10.0, 7.0)),
        (15, 20),
    )
    return [Candidate(a, b, c, d, pair[0], pair[1], hold) for a, b, c, d, pair, hold in values]


def pct_change(new: float, old: float) -> float:
    return 0.0 if old == 0 else (new / old - 1.0) * 100.0


def choose_strike(options: pd.DataFrame, spot: float) -> int | None:
    ce = set(options.loc[options.option_type.eq("CE"), "strike"].astype(int))
    pe = set(options.loc[options.option_type.eq("PE"), "strike"].astype(int))
    common = ce & pe
    return min(common, key=lambda x: abs(x - spot)) if common else None


def contract(options: pd.DataFrame, strike: int, side: str) -> pd.DataFrame:
    frame = options[(options.strike.eq(strike)) & (options.option_type.eq(side))].copy()
    return frame.sort_values("timestamp").reset_index(drop=True)


def option_features(frame: pd.DataFrame, signal_dt: pd.Timestamp) -> dict | None:
    rows = frame.index[frame.timestamp.eq(signal_dt)].tolist()
    if not rows:
        return None
    i = int(rows[0])
    if i < 3:
        return None
    now = frame.iloc[i]
    prev3 = frame.iloc[i - 3]
    prior = frame.iloc[max(0, i - 10):i]
    if prior.empty:
        return None
    avg_vol = float(prior.volume.mean())
    return {
        "premium_move_pct": pct_change(float(now.close), float(prev3.close)),
        "volume_ratio": float(now.volume) / avg_vol if avg_vol > 0 else 0.0,
        "oi_change_pct": pct_change(float(now.open_interest), float(prev3.open_interest)) if float(prev3.open_interest) else 0.0,
    }


def find_retest(day_spot: pd.DataFrame, side: str, level: float) -> tuple[pd.Timestamp, float] | None:
    after = day_spot[(day_spot.minute >= ENTRY_START) & (day_spot.minute <= LAST_SIGNAL)].reset_index(drop=True)
    breakout_i = None
    for i, row in after.iterrows():
        close = float(row.close)
        if side == "CE" and close >= level * 1.0004:
            breakout_i = i
            break
        if side == "PE" and close <= level * 0.9996:
            breakout_i = i
            break
    if breakout_i is None:
        return None
    breakout_dt = pd.Timestamp(after.iloc[breakout_i].timestamp)
    deadline = breakout_dt + pd.Timedelta(minutes=10)
    for _, row in after[(after.timestamp > breakout_dt) & (after.timestamp <= deadline)].iterrows():
        close, low, high = float(row.close), float(row.low), float(row.high)
        if side == "CE" and low <= level * 1.0004 and close > level:
            return pd.Timestamp(row.timestamp), close
        if side == "PE" and high >= level * 0.9996 and close < level:
            return pd.Timestamp(row.timestamp), close
    return None


def exit_trade(frame: pd.DataFrame, signal_dt: pd.Timestamp, c: Candidate) -> dict | None:
    entry_dt = signal_dt + pd.Timedelta(minutes=1)
    rows = frame.index[frame.timestamp.eq(entry_dt)].tolist()
    if not rows:
        return None
    i = int(rows[0])
    entry = float(frame.iloc[i].open) + ENTRY_SLIPPAGE
    target = entry + c.target_points
    stop = entry - c.stop_points
    hard_end = min(signal_dt + pd.Timedelta(minutes=c.max_hold_minutes), signal_dt.normalize() + pd.Timedelta(hours=12))
    path = frame[(frame.timestamp >= entry_dt) & (frame.timestamp <= hard_end)]
    if path.empty:
        return None
    exit_price = float(path.iloc[-1].close)
    exit_dt = pd.Timestamp(path.iloc[-1].timestamp)
    outcome = "TIMEOUT"
    for _, bar in path.iterrows():
        hit_stop = float(bar.low) <= stop
        hit_target = float(bar.high) >= target
        if hit_stop:
            exit_price, exit_dt, outcome = stop, pd.Timestamp(bar.timestamp), "LOSS"
            break
        if hit_target:
            exit_price, exit_dt, outcome = target, pd.Timestamp(bar.timestamp), "WIN"
            break
    pnl = exit_price - entry - FRICTION
    if outcome == "TIMEOUT":
        outcome = "WIN" if pnl > 0 else "LOSS"
    return {"entry_dt": entry_dt, "exit_dt": exit_dt, "entry": entry, "exit": exit_price, "pnl": pnl, "outcome": outcome}


def build_contexts() -> list[dict]:
    path = _download_public_sample(Path("/tmp/nandi_morning_orb_open/nifty_1y_1min.xlsx"))
    spot = _parse_spot_frame(path)
    options = _parse_option_frame(path)
    spot = spot[(spot.timestamp.dt.date >= START) & (spot.timestamp.dt.date <= END)].copy()
    options = options[(options.day >= START) & (options.day <= END)].copy()
    spot["day"] = spot.timestamp.dt.date
    spot["minute"] = spot.timestamp.dt.hour * 60 + spot.timestamp.dt.minute
    contexts = []
    for day, day_spot in spot.groupby("day", sort=True):
        day_spot = day_spot.sort_values("timestamp").reset_index(drop=True)
        opening = day_spot[(day_spot.minute >= OPEN_START) & (day_spot.minute <= OPEN_END)]
        day_options = options[options.day.eq(day)].copy()
        if len(opening) < 10 or day_options.empty:
            continue
        contexts.append({
            "day": day,
            "spot": day_spot,
            "options": day_options,
            "or_high": float(opening.high.max()),
            "or_low": float(opening.low.min()),
        })
    return contexts


def evaluate(contexts: list[dict], c: Candidate, start: date, end: date) -> tuple[dict, pd.DataFrame]:
    records = []
    for ctx in contexts:
        day = ctx["day"]
        if day < start or day > end:
            continue
        best = None
        for side, level in (("CE", ctx["or_high"]), ("PE", ctx["or_low"])):
            retest = find_retest(ctx["spot"], side, level)
            if retest is not None and (best is None or retest[0] < best[0]):
                best = (retest[0], retest[1], side)
        if best is None:
            continue
        signal_dt, signal_spot, side = best
        strike = choose_strike(ctx["options"], signal_spot)
        if strike is None:
            continue
        target = contract(ctx["options"], strike, side)
        opposite = contract(ctx["options"], strike, "PE" if side == "CE" else "CE")
        tf, of = option_features(target, signal_dt), option_features(opposite, signal_dt)
        if tf is None or of is None:
            continue
        outperf = tf["premium_move_pct"] - of["premium_move_pct"]
        if tf["premium_move_pct"] < c.min_option_move_pct:
            continue
        if outperf < c.min_option_outperformance_pct:
            continue
        if tf["volume_ratio"] < c.min_volume_ratio:
            continue
        if abs(tf["oi_change_pct"]) < c.min_abs_oi_change_pct:
            continue
        ex = exit_trade(target, signal_dt, c)
        if ex is None:
            continue
        expiry_values = target.expiry.dropna()
        records.append({
            "day": day,
            "signal_dt": signal_dt,
            "side": side,
            "strike": strike,
            "expiry": str(expiry_values.iloc[0]) if not expiry_values.empty else "",
            "signal_spot": signal_spot,
            "option_move_pct": round(tf["premium_move_pct"], 4),
            "option_outperformance_pct": round(outperf, 4),
            "volume_ratio": round(tf["volume_ratio"], 4),
            "oi_change_pct": round(tf["oi_change_pct"], 4),
            **ex,
        })
    trades = pd.DataFrame(records)
    if trades.empty:
        trades = pd.DataFrame(columns=["day", "signal_dt", "pnl"])
    return metrics(trades), trades


def score(stats: dict) -> float:
    if stats["trades"] < 20:
        return -1e12
    wr = float(stats["win_rate"] or 0)
    pf = float(stats["profit_factor"] or 0)
    exp = float(stats["expectancy"] or -999)
    net = float(stats["net_points"] or 0)
    if exp <= 0 or pf <= 1 or net <= 0:
        return -1e10 + net
    return wr * 3 + min(pf, 4) * 20 + exp * 10 + net * 0.05


def monthly(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    t = trades.copy()
    t["month"] = pd.to_datetime(t.signal_dt).dt.to_period("M").astype(str)
    out = []
    for month, g in t.groupby("month", sort=True):
        row = {"month": month}
        row.update(metrics(g))
        out.append(row)
    return out


def main() -> None:
    contexts = build_contexts()
    candidates = grid()
    ranked = []
    for c in candidates:
        s, _ = evaluate(contexts, c, START, TRAIN_END)
        ranked.append((score(s), c, s))
    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score, chosen, train = ranked[0]
    if best_score <= -1e11:
        payload = {
            "search_name": "Morning ORB V2 Open Data 8M",
            "data_source": PUBLIC_SAMPLE_PROJECT,
            "license": PUBLIC_SAMPLE_LICENSE,
            "window": [START.isoformat(), END.isoformat()],
            "candidate_count": len(candidates),
            "status": "NO_TRAINING_CANDIDATE",
            "training": train,
        }
        Path("morning_orb_open_8m.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return

    train, train_trades = evaluate(contexts, chosen, START, TRAIN_END)
    valid, valid_trades = evaluate(contexts, chosen, VALID_START, VALID_END)
    stress, stress_trades = evaluate(contexts, chosen, STRESS_START, STRESS_END)
    all_trades = pd.concat([train_trades, valid_trades, stress_trades], ignore_index=True)
    passed = (
        valid["trades"] >= 20 and float(valid["win_rate"] or 0) >= 70 and float(valid["profit_factor"] or 0) >= 1.5
        and float(valid["expectancy"] or 0) > 0 and float(valid["net_points"] or 0) > 0
        and stress["trades"] >= 15 and float(stress["win_rate"] or 0) >= 60 and float(stress["profit_factor"] or 0) >= 1.2
        and float(stress["expectancy"] or 0) > 0 and float(stress["net_points"] or 0) > 0
    )
    payload = {
        "search_name": "Morning ORB V2 Open Data 8M",
        "data_source": PUBLIC_SAMPLE_PROJECT,
        "license": PUBLIC_SAMPLE_LICENSE,
        "window": [START.isoformat(), END.isoformat()],
        "selection_window": [START.isoformat(), TRAIN_END.isoformat()],
        "validation_window": [VALID_START.isoformat(), VALID_END.isoformat()],
        "stress_window": [STRESS_START.isoformat(), STRESS_END.isoformat()],
        "candidate_count": len(candidates),
        "chosen": asdict(chosen),
        "training": train,
        "validation": valid,
        "stress": stress,
        "all_8m": metrics(all_trades),
        "monthly": monthly(all_trades),
        "status": "OPEN_DATA_PASS_TO_UPSTOX" if passed else "OPEN_DATA_REJECT",
        "note": "Open data is screening evidence only. No production merge or live-trading claim.",
    }
    Path("morning_orb_open_8m.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    all_trades.to_csv("morning_orb_open_8m_trades.csv", index=False)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
