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

TREND_START = 9 * 60 + 15
TREND_END = 9 * 60 + 44
SIGNAL_START = 9 * 60 + 45
LAST_SIGNAL = 11 * 60 + 29
ENTRY_SLIPPAGE = 0.20
FRICTION = 0.50

# Frozen price mechanics. Search is limited to option confirmation and exits.
MIN_TREND_MOVE_PCT = 0.12
MIN_CLOSE_POSITION = 0.65
PULLBACK_MIN_PCT = 0.04
PULLBACK_MAX_PCT = 0.16
RESUME_MINUTES = 10


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
        (0.20, 0.50),
        (0.20, 0.50),
        (0.90, 1.20),
        (0.00, 0.20),
        ((6.0, 5.0), (8.0, 6.0), (10.0, 7.0)),
        (20, 30),
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


def opening_trend(day_spot: pd.DataFrame) -> str | None:
    opening = day_spot[(day_spot.minute >= TREND_START) & (day_spot.minute <= TREND_END)]
    if len(opening) < 25:
        return None
    first_open = float(opening.iloc[0].open)
    last_close = float(opening.iloc[-1].close)
    high = float(opening.high.max())
    low = float(opening.low.min())
    if first_open <= 0 or high <= low:
        return None
    move_pct = pct_change(last_close, first_open)
    close_position = (last_close - low) / (high - low)
    if move_pct >= MIN_TREND_MOVE_PCT and close_position >= MIN_CLOSE_POSITION:
        return "CE"
    if move_pct <= -MIN_TREND_MOVE_PCT and close_position <= 1.0 - MIN_CLOSE_POSITION:
        return "PE"
    return None


def find_pullback_resume(day_spot: pd.DataFrame, side: str) -> tuple[pd.Timestamp, float] | None:
    after = day_spot[(day_spot.minute >= SIGNAL_START) & (day_spot.minute <= LAST_SIGNAL)].reset_index(drop=True)
    if len(after) < 6:
        return None

    if side == "CE":
        anchor = float(day_spot[day_spot.minute <= TREND_END].high.max())
        for i, row in after.iterrows():
            anchor = max(anchor, float(row.high))
            depth = (anchor - float(row.low)) / anchor * 100.0 if anchor else 0.0
            if not (PULLBACK_MIN_PCT <= depth <= PULLBACK_MAX_PCT):
                continue
            deadline = min(len(after) - 1, i + RESUME_MINUTES)
            for j in range(i + 1, deadline + 1):
                current = after.iloc[j]
                prior = after.iloc[max(0, j - 3):j]
                if prior.empty:
                    continue
                prior_high = float(prior.high.max())
                if float(current.close) > prior_high and float(current.close) > float(current.open):
                    return pd.Timestamp(current.timestamp), float(current.close)
    else:
        anchor = float(day_spot[day_spot.minute <= TREND_END].low.min())
        for i, row in after.iterrows():
            anchor = min(anchor, float(row.low))
            depth = (float(row.high) - anchor) / anchor * 100.0 if anchor else 0.0
            if not (PULLBACK_MIN_PCT <= depth <= PULLBACK_MAX_PCT):
                continue
            deadline = min(len(after) - 1, i + RESUME_MINUTES)
            for j in range(i + 1, deadline + 1):
                current = after.iloc[j]
                prior = after.iloc[max(0, j - 3):j]
                if prior.empty:
                    continue
                prior_low = float(prior.low.min())
                if float(current.close) < prior_low and float(current.close) < float(current.open):
                    return pd.Timestamp(current.timestamp), float(current.close)
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
        if hit_stop and hit_target:
            exit_price, exit_dt, outcome = stop, pd.Timestamp(bar.timestamp), "LOSS"
            break
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
    path = _download_public_sample(Path("/tmp/nandi_morning_trend_pullback/nifty_1y_1min.xlsx"))
    spot = _parse_spot_frame(path)
    options = _parse_option_frame(path)
    spot = spot[(spot.timestamp.dt.date >= START) & (spot.timestamp.dt.date <= END)].copy()
    options = options[(options.day >= START) & (options.day <= END)].copy()
    spot["day"] = spot.timestamp.dt.date
    spot["minute"] = spot.timestamp.dt.hour * 60 + spot.timestamp.dt.minute

    contexts = []
    for day, day_spot in spot.groupby("day", sort=True):
        day_spot = day_spot.sort_values("timestamp").reset_index(drop=True)
        day_options = options[options.day.eq(day)].copy()
        if day_options.empty:
            continue
        side = opening_trend(day_spot)
        if side is None:
            continue
        signal = find_pullback_resume(day_spot, side)
        if signal is None:
            continue
        contexts.append({
            "day": day,
            "spot": day_spot,
            "options": day_options,
            "side": side,
            "signal_dt": signal[0],
            "signal_spot": signal[1],
        })
    return contexts


def evaluate(contexts: list[dict], c: Candidate, start: date, end: date) -> tuple[dict, pd.DataFrame]:
    records: list[dict] = []
    for ctx in contexts:
        day = ctx["day"]
        if day < start or day > end:
            continue
        side = ctx["side"]
        signal_dt = ctx["signal_dt"]
        signal_spot = ctx["signal_spot"]
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
        records.append({
            "day": day,
            "signal_dt": signal_dt,
            "side": side,
            "strike": strike,
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
    trades = int(stats.get("trades") or 0)
    if trades < 12:
        return -1e12
    wr = float(stats.get("win_rate") or 0.0)
    pf = float(stats.get("profit_factor") or 0.0)
    exp = float(stats.get("expectancy") or -999.0)
    net = float(stats.get("net_points") or 0.0)
    if exp <= 0 or pf <= 1.0 or net <= 0:
        return -1e10 + net
    return wr * 3.0 + min(pf, 4.0) * 20.0 + exp * 10.0 + net * 0.05 + min(trades, 30) * 0.25


def monthly(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    temp = trades.copy()
    temp["month"] = pd.to_datetime(temp.signal_dt).dt.to_period("M").astype(str)
    rows = []
    for month, group in temp.groupby("month", sort=True):
        row = {"month": month}
        row.update(metrics(group))
        rows.append(row)
    return rows


def main() -> None:
    contexts = build_contexts()
    candidates = grid()
    ranked = []
    for c in candidates:
        train_stats, _ = evaluate(contexts, c, START, TRAIN_END)
        ranked.append((score(train_stats), c, train_stats))
    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score, chosen, train = ranked[0]

    train, train_trades = evaluate(contexts, chosen, START, TRAIN_END)
    valid, valid_trades = evaluate(contexts, chosen, VALID_START, VALID_END)
    stress, stress_trades = evaluate(contexts, chosen, STRESS_START, STRESS_END)
    all_trades = pd.concat([train_trades, valid_trades, stress_trades], ignore_index=True)

    training_ok = best_score > -1e11
    passed = (
        training_ok
        and valid["trades"] >= 10
        and float(valid["win_rate"] or 0) >= 70
        and float(valid["profit_factor"] or 0) >= 1.5
        and float(valid["expectancy"] or 0) > 0
        and float(valid["net_points"] or 0) > 0
        and stress["trades"] >= 8
        and float(stress["win_rate"] or 0) >= 60
        and float(stress["profit_factor"] or 0) >= 1.2
        and float(stress["expectancy"] or 0) > 0
        and float(stress["net_points"] or 0) > 0
    )

    payload = {
        "search_name": "Morning Trend Pullback Open Data 8M",
        "data_source": PUBLIC_SAMPLE_PROJECT,
        "license": PUBLIC_SAMPLE_LICENSE,
        "window": [START.isoformat(), END.isoformat()],
        "selection_window": [START.isoformat(), TRAIN_END.isoformat()],
        "validation_window": [VALID_START.isoformat(), VALID_END.isoformat()],
        "stress_window": [STRESS_START.isoformat(), STRESS_END.isoformat()],
        "fixed_price_pattern": {
            "trend_window": "09:15-09:44 IST",
            "signal_window": "09:45-11:29 IST",
            "min_trend_move_pct": MIN_TREND_MOVE_PCT,
            "min_close_position": MIN_CLOSE_POSITION,
            "pullback_min_pct": PULLBACK_MIN_PCT,
            "pullback_max_pct": PULLBACK_MAX_PCT,
            "resume_minutes": RESUME_MINUTES,
            "entry": "next minute",
            "max_one_trade_per_day": True,
        },
        "price_context_days": len(contexts),
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
    Path("morning_trend_pullback_open_8m.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    all_trades.to_csv("morning_trend_pullback_open_8m_trades.csv", index=False)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
