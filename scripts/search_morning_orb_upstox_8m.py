from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nandi_v2.profit_first import UpstoxProfitFirstHistory, choose_atm_strike, metrics

START = date(2026, 1, 1)
END = date(2026, 8, 28)
TRAIN_END = date(2026, 4, 30)
VALID_START = date(2026, 5, 1)
VALID_END = date(2026, 6, 30)
STRESS_START = date(2026, 7, 1)
STRESS_END = END

OPEN_START = 9 * 60 + 15
OPEN_END = 9 * 60 + 29
ENTRY_START = 9 * 60 + 30
LAST_SIGNAL = 11 * 60 + 44

ENTRY_SLIPPAGE = 0.20
FRICTION = 0.50


@dataclass(frozen=True)
class Candidate:
    breakout_pct: float
    retest_tolerance_pct: float
    retest_minutes: int
    min_option_move_pct: float
    min_option_outperformance_pct: float
    min_volume_ratio: float
    min_abs_oi_change_pct: float
    target_points: float
    stop_points: float
    max_hold_minutes: int


def candidate_grid() -> list[Candidate]:
    # Keep the opening mechanics fixed to avoid data-mining the price pattern.
    # Only a small bounded confirmation/exit grid is selected on Jan-Apr.
    values = itertools.product(
        (0.04,),
        (0.04,),
        (10,),
        (0.40, 0.70),
        (0.40, 0.70),
        (1.00, 1.30),
        (0.00, 0.20),
        ((6.0, 5.0), (8.0, 6.0), (10.0, 7.0)),
        (15, 20),
    )
    return [
        Candidate(a, b, c, d, e, f, g, pair[0], pair[1], hold)
        for a, b, c, d, e, f, g, pair, hold in values
    ]


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new / old - 1.0) * 100.0


def _option_features(frame: pd.DataFrame, signal_dt: pd.Timestamp) -> dict | None:
    current_rows = frame.index[frame["dt"] == signal_dt].tolist()
    if not current_rows:
        return None
    i = int(current_rows[0])
    if i < 10:
        return None
    now = frame.iloc[i]
    three = frame.iloc[max(0, i - 3)]
    prior = frame.iloc[max(0, i - 10):i]
    if prior.empty:
        return None
    prior_volume = float(prior["Volume"].mean())
    volume_ratio = float(now["Volume"]) / prior_volume if prior_volume > 0 else 0.0
    return {
        "premium_move_pct": pct_change(float(now["Close"]), float(three["Close"])),
        "volume_ratio": volume_ratio,
        "oi_change_pct": pct_change(float(now["OI"]), float(three["OI"])) if float(three["OI"]) else 0.0,
    }


def _find_retest(
    day_spot: pd.DataFrame,
    direction: str,
    level: float,
    breakout_pct: float,
    tolerance_pct: float,
    retest_minutes: int,
) -> tuple[pd.Timestamp, float] | None:
    after = day_spot[
        (day_spot["minute"] >= ENTRY_START)
        & (day_spot["minute"] <= LAST_SIGNAL)
    ].reset_index(drop=True)
    breakout_i = None
    for i, row in after.iterrows():
        close = float(row["Close"])
        if direction == "CE" and close >= level * (1.0 + breakout_pct / 100.0):
            breakout_i = i
            break
        if direction == "PE" and close <= level * (1.0 - breakout_pct / 100.0):
            breakout_i = i
            break
    if breakout_i is None:
        return None

    breakout_dt = pd.Timestamp(after.iloc[breakout_i]["dt"])
    deadline = breakout_dt + pd.Timedelta(minutes=retest_minutes)
    subset = after[
        (after["dt"] > breakout_dt)
        & (after["dt"] <= deadline)
        & (after["minute"] <= LAST_SIGNAL)
    ]
    for _, row in subset.iterrows():
        close = float(row["Close"])
        low = float(row["Low"])
        high = float(row["High"])
        if direction == "CE":
            touched = low <= level * (1.0 + tolerance_pct / 100.0)
            held = close > level
        else:
            touched = high >= level * (1.0 - tolerance_pct / 100.0)
            held = close < level
        if touched and held:
            return pd.Timestamp(row["dt"]), close
    return None


def _exit_trade(
    option: pd.DataFrame,
    signal_dt: pd.Timestamp,
    target_points: float,
    stop_points: float,
    max_hold_minutes: int,
) -> dict | None:
    entry_dt = signal_dt + pd.Timedelta(minutes=1)
    rows = option.index[option["dt"] == entry_dt].tolist()
    if not rows:
        return None
    i = int(rows[0])
    entry = float(option.loc[i, "Open"]) + ENTRY_SLIPPAGE
    target = entry + target_points
    stop = entry - stop_points
    hard_end = min(
        signal_dt + pd.Timedelta(minutes=max_hold_minutes),
        signal_dt.normalize() + pd.Timedelta(hours=12),
    )
    path = option[(option["dt"] >= entry_dt) & (option["dt"] <= hard_end)]
    if path.empty:
        return None

    exit_price = float(path.iloc[-1]["Close"])
    exit_dt = pd.Timestamp(path.iloc[-1]["dt"])
    outcome = "TIMEOUT"
    for _, bar in path.iterrows():
        hit_stop = float(bar["Low"]) <= stop
        hit_target = float(bar["High"]) >= target
        # Conservative ambiguity: if both occur in the same minute, count the stop first.
        if hit_stop and hit_target:
            exit_price = stop
            exit_dt = pd.Timestamp(bar["dt"])
            outcome = "LOSS"
            break
        if hit_stop:
            exit_price = stop
            exit_dt = pd.Timestamp(bar["dt"])
            outcome = "LOSS"
            break
        if hit_target:
            exit_price = target
            exit_dt = pd.Timestamp(bar["dt"])
            outcome = "WIN"
            break

    pnl = exit_price - entry - FRICTION
    if outcome == "TIMEOUT":
        outcome = "WIN" if pnl > 0 else "LOSS"
    return {
        "entry_dt": entry_dt,
        "exit_dt": exit_dt,
        "entry": entry,
        "exit": exit_price,
        "pnl": pnl,
        "outcome": outcome,
    }


def build_day_contexts(client: UpstoxProfitFirstHistory) -> list[dict]:
    spot = client._spot_history(START, END)
    if spot.empty:
        raise RuntimeError("Upstox returned no NIFTY spot candles for the 8-month research window")
    spot["day"] = spot["dt"].dt.date
    spot["minute"] = spot["dt"].dt.hour * 60 + spot["dt"].dt.minute

    expiries = client._all_expiries()
    if not expiries:
        raise RuntimeError("Upstox returned no NIFTY option expiries")

    today = date.today()
    contract_cache: dict[tuple[date, bool], dict] = {}
    option_cache: dict[tuple[str, date, bool], pd.DataFrame] = {}
    contexts: list[dict] = []

    for day, day_spot in spot.groupby("day", sort=True):
        if day < START or day > END:
            continue
        day_spot = day_spot.sort_values("dt").reset_index(drop=True)
        opening = day_spot[
            (day_spot["minute"] >= OPEN_START)
            & (day_spot["minute"] <= OPEN_END)
        ]
        if len(opening) < 10:
            continue
        or_high = float(opening["High"].max())
        or_low = float(opening["Low"].min())

        possible = [expiry for expiry in expiries if expiry >= day]
        if not possible:
            continue
        expiry = min(possible)
        expired = expiry < today
        ckey = (expiry, expired)
        if ckey not in contract_cache:
            contract_cache[ckey] = client._contracts(expiry, expired)
        contracts = contract_cache[ckey]
        if not contracts:
            continue

        contexts.append({
            "day": day,
            "spot": day_spot,
            "or_high": or_high,
            "or_low": or_low,
            "expiry": expiry,
            "expired": expired,
            "contracts": contracts,
            "option_cache": option_cache,
        })
    return contexts


def evaluate_candidate(
    client: UpstoxProfitFirstHistory,
    contexts: list[dict],
    candidate: Candidate,
    start: date,
    end: date,
) -> tuple[dict, pd.DataFrame]:
    records: list[dict] = []

    for ctx in contexts:
        day = ctx["day"]
        if day < start or day > end:
            continue

        best_signal = None
        for side, level in (("CE", ctx["or_high"]), ("PE", ctx["or_low"])):
            retest = _find_retest(
                ctx["spot"],
                side,
                level,
                candidate.breakout_pct,
                candidate.retest_tolerance_pct,
                candidate.retest_minutes,
            )
            if retest is None:
                continue
            signal_dt, signal_spot = retest
            if best_signal is None or signal_dt < best_signal[0]:
                best_signal = (signal_dt, signal_spot, side)

        if best_signal is None:
            continue
        signal_dt, signal_spot, side = best_signal

        contracts = ctx["contracts"]
        strike = choose_atm_strike(tuple(contracts), signal_spot)
        legs = contracts[strike]
        target_key = legs[side]
        opposite = "PE" if side == "CE" else "CE"
        opposite_key = legs[opposite]
        expired = ctx["expired"]
        option_cache = ctx["option_cache"]

        def get_option(key: str) -> pd.DataFrame:
            cache_key = (key, day, expired)
            if cache_key not in option_cache:
                option_cache[cache_key] = client._option_history(key, day, expired)
            return option_cache[cache_key]

        target_opt = get_option(target_key)
        opposite_opt = get_option(opposite_key)
        if target_opt.empty or opposite_opt.empty:
            continue

        target_f = _option_features(target_opt, signal_dt)
        opposite_f = _option_features(opposite_opt, signal_dt)
        if target_f is None or opposite_f is None:
            continue

        if target_f["premium_move_pct"] < candidate.min_option_move_pct:
            continue
        outperformance = target_f["premium_move_pct"] - opposite_f["premium_move_pct"]
        if outperformance < candidate.min_option_outperformance_pct:
            continue
        if target_f["volume_ratio"] < candidate.min_volume_ratio:
            continue
        if abs(target_f["oi_change_pct"]) < candidate.min_abs_oi_change_pct:
            continue

        exit_info = _exit_trade(
            target_opt,
            signal_dt,
            candidate.target_points,
            candidate.stop_points,
            candidate.max_hold_minutes,
        )
        if exit_info is None:
            continue
        records.append({
            "day": day,
            "signal_dt": signal_dt,
            "side": side,
            "strike": strike,
            "expiry": ctx["expiry"].isoformat(),
            "signal_spot": signal_spot,
            "option_move_pct": round(target_f["premium_move_pct"], 4),
            "opposite_move_pct": round(opposite_f["premium_move_pct"], 4),
            "option_outperformance_pct": round(outperformance, 4),
            "volume_ratio": round(target_f["volume_ratio"], 4),
            "oi_change_pct": round(target_f["oi_change_pct"], 4),
            **exit_info,
        })

    trades = pd.DataFrame(records)
    if trades.empty:
        trades = pd.DataFrame(columns=["day", "signal_dt", "side", "strike", "expiry", "pnl", "outcome"])
    return metrics(trades), trades


def training_score(stats: dict) -> float:
    if stats["trades"] < 25:
        return -1e12
    wr = float(stats["win_rate"] or 0.0)
    pf = float(stats["profit_factor"] or 0.0)
    exp = float(stats["expectancy"] or -999.0)
    net = float(stats["net_points"] or 0.0)
    if exp <= 0 or pf <= 1.0 or net <= 0:
        return -1e10 + net
    return wr * 3.0 + min(pf, 4.0) * 20.0 + exp * 10.0 + net * 0.05


def period_summary(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return metrics(trades)
    return metrics(trades.sort_values("signal_dt").reset_index(drop=True))


def monthly_rows(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    temp = trades.copy()
    temp["month"] = pd.to_datetime(temp["signal_dt"]).dt.to_period("M").astype(str)
    rows = []
    for month, group in temp.groupby("month", sort=True):
        row = {"month": month}
        row.update(period_summary(group))
        rows.append(row)
    return rows


def main() -> None:
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("UPSTOX_ACCESS_TOKEN is required; no fallback/public data is permitted")

    client = UpstoxProfitFirstHistory(token, timeout_seconds=45.0)
    contexts = build_day_contexts(client)
    if not contexts:
        raise SystemExit("No valid Upstox trading-day contexts were built")

    grid = candidate_grid()
    ranked = []
    for candidate in grid:
        stats, _ = evaluate_candidate(client, contexts, candidate, START, TRAIN_END)
        score = training_score(stats)
        if score > -1e11:
            ranked.append((score, candidate, stats))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        payload = {
            "search_name": "Morning ORB V2 Upstox 8M",
            "data_source": "UPSTOX_ONLY",
            "window": [START.isoformat(), END.isoformat()],
            "status": "NO_TRAINING_CANDIDATE",
            "candidate_count": len(grid),
            "days_built": len(contexts),
        }
        Path("morning_orb_upstox_8m.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return

    _, chosen, train_stats = ranked[0]
    train_stats, train_trades = evaluate_candidate(client, contexts, chosen, START, TRAIN_END)
    valid_stats, valid_trades = evaluate_candidate(client, contexts, chosen, VALID_START, VALID_END)
    stress_stats, stress_trades = evaluate_candidate(client, contexts, chosen, STRESS_START, STRESS_END)
    all_trades = pd.concat([train_trades, valid_trades, stress_trades], ignore_index=True)
    all_stats = period_summary(all_trades)

    validation_pass = (
        valid_stats["trades"] >= 20
        and float(valid_stats["win_rate"] or 0.0) >= 70.0
        and float(valid_stats["expectancy"] or -1.0) > 0.0
        and float(valid_stats["profit_factor"] or 0.0) >= 1.5
        and float(valid_stats["net_points"] or 0.0) > 0.0
    )
    stress_pass = (
        stress_stats["trades"] >= 15
        and float(stress_stats["win_rate"] or 0.0) >= 60.0
        and float(stress_stats["expectancy"] or -1.0) > 0.0
        and float(stress_stats["profit_factor"] or 0.0) >= 1.2
        and float(stress_stats["net_points"] or 0.0) > 0.0
    )

    payload = {
        "search_name": "Morning ORB V2 Upstox 8M",
        "data_source": "UPSTOX_ONLY",
        "market": "NIFTY ATM options",
        "window": [START.isoformat(), END.isoformat()],
        "session_rule": "09:15-09:29 opening range; signals 09:30-11:44; forced exit by 12:00 IST",
        "selection_protocol": {
            "training": [START.isoformat(), TRAIN_END.isoformat()],
            "validation": [VALID_START.isoformat(), VALID_END.isoformat()],
            "stress": [STRESS_START.isoformat(), STRESS_END.isoformat()],
            "candidate_frozen_before_validation": True,
        },
        "candidate_count": len(grid),
        "days_built": len(contexts),
        "chosen_candidate": asdict(chosen),
        "training": train_stats,
        "validation": valid_stats,
        "stress": stress_stats,
        "all_8_months_descriptive": all_stats,
        "monthly": monthly_rows(all_trades),
        "validation_pass": validation_pass,
        "stress_pass": stress_pass,
        "overall_status": "ELIGIBLE_FOR_FORWARD_PAPER" if validation_pass and stress_pass else "RESEARCH_FAILED_GATE",
        "gate": "Validation >=20 trades, >=70% wins, PF>=1.5, positive expectancy/net; stress >=15 trades, >=60% wins, PF>=1.2, positive expectancy/net.",
        "note": "Training months are descriptive/in-sample. May-Jun and Jul-Aug are the independent evidence used for the gate.",
        "trades": [
            {
                k: (v.isoformat() if isinstance(v, (pd.Timestamp, date)) else v)
                for k, v in row.items()
            }
            for row in all_trades.to_dict(orient="records")
        ],
    }
    Path("morning_orb_upstox_8m.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    all_trades.to_csv("morning_orb_upstox_8m_trades.csv", index=False)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
