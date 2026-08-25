from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

HF_REPO = "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m"
HF_RESOLVE = HF_REPO + "/resolve/main"
MAX_DTE = 7
TARGET_POINTS = 10.0
STOP_POINTS = 5.0
HORIZON_MINUTES = 15
GATES = ("breakout", "pullback_reclaim", "micro_break")


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1000:
        return path
    req = Request(url, headers={"User-Agent": "Shiv-Precision-Research/1.0"})
    with urlopen(req, timeout=180) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    return path


def _normalize_timestamp(values: pd.Series) -> pd.Series:
    ts = pd.to_datetime(values, errors="coerce")
    try:
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return ts


def load_frames(start: date, end: date, cache: Path):
    index_path = _download(HF_RESOLVE + "/index/NIFTY.parquet?download=true", cache / "NIFTY.parquet")
    idx = pd.read_parquet(index_path)
    ts = _normalize_timestamp(idx["timestamp"])
    spot = pd.DataFrame({
        "timestamp": ts,
        "open": pd.to_numeric(idx["open"], errors="coerce"),
        "high": pd.to_numeric(idx["high"], errors="coerce"),
        "low": pd.to_numeric(idx["low"], errors="coerce"),
        "close": pd.to_numeric(idx["close"], errors="coerce"),
        "volume": pd.to_numeric(idx.get("volume", 0), errors="coerce").fillna(0),
    }).dropna(subset=["timestamp", "open", "high", "low", "close"])
    spot["day"] = spot["timestamp"].dt.date
    spot = spot[(spot.day >= start) & (spot.day <= end)].sort_values("timestamp")

    expiries = []
    cursor = start
    while cursor <= end + timedelta(days=7):
        if cursor.weekday() == 1:  # Tuesday NIFTY weekly expiry regime in 2026.
            expiries.append(cursor)
        cursor += timedelta(days=1)

    frames = []
    loaded = []
    for expiry in expiries:
        name = expiry.isoformat() + ".parquet"
        path = cache / "options" / name
        try:
            _download(HF_RESOLVE + f"/options/NIFTY/{name}?download=true", path)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        raw = pd.read_parquet(path)
        if raw.empty:
            continue
        loaded.append(expiry.isoformat())
        ots = _normalize_timestamp(raw["timestamp"])
        side_source = raw["option_type"] if "option_type" in raw else raw.get("right", "")
        side = side_source.astype(str).str.upper().str.strip().replace({"CALL": "CE", "PUT": "PE", "C": "CE", "P": "PE"})
        exp_col = pd.to_datetime(raw["expiry"], errors="coerce").dt.date if "expiry" in raw else pd.Series([expiry] * len(raw))
        frame = pd.DataFrame({
            "timestamp": ots,
            "day": ots.dt.date,
            "expiry": exp_col,
            "strike": pd.to_numeric(raw["strike"], errors="coerce"),
            "side": side,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw.get("volume", 0), errors="coerce").fillna(0),
            "oi": pd.to_numeric(raw.get("open_interest", raw.get("oi", 0)), errors="coerce").fillna(0),
        }).dropna(subset=["timestamp", "expiry", "strike", "open", "high", "low", "close"])
        frame = frame[(frame.day >= start) & (frame.day <= end) & frame.side.isin(["CE", "PE"])]
        frame["strike"] = frame["strike"].astype(int)
        frames.append(frame)
    if not frames:
        raise RuntimeError("No NIFTY option files available for requested range")
    options = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["timestamp", "expiry", "strike", "side"]).sort_values(["timestamp", "strike", "side"])
    return spot, options, loaded


def _bucket_start(ts: datetime, minutes: int) -> datetime:
    anchor = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    elapsed = int((ts - anchor).total_seconds() // 60)
    return anchor + timedelta(minutes=(max(0, elapsed) // minutes) * minutes)


def aggregate(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy().sort_values("timestamp")
    work["bucket"] = work["timestamp"].map(lambda x: _bucket_start(x, minutes))
    grouped = work.groupby("bucket", sort=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index().rename(columns={"bucket": "timestamp"})
    if "oi" in work.columns:
        oi = grouped["oi"].last().reset_index(drop=True)
        out["oi"] = oi
    return out


def completed(df: pd.DataFrame, now: datetime, minutes: int) -> pd.DataFrame:
    return df[df.timestamp + pd.to_timedelta(minutes, unit="m") <= now]


def _pct_move(series: pd.Series, bars: int = 3) -> float:
    if len(series) <= bars:
        return 0.0
    a = float(series.iloc[-bars-1])
    b = float(series.iloc[-1])
    return 0.0 if a <= 0 else 100.0 * (b / a - 1.0)


def _efficiency(closes: pd.Series, bars: int = 5) -> float:
    if len(closes) < bars + 1:
        return 0.0
    x = closes.iloc[-bars-1:].astype(float).to_numpy()
    denom = float(np.abs(np.diff(x)).sum())
    return 0.0 if denom <= 1e-9 else float(abs(x[-1] - x[0]) / denom)


def _structure(df: pd.DataFrame, side: str) -> bool:
    if len(df) < 3:
        return False
    r = df.iloc[-3:]
    if side == "CE":
        return bool(r.high.iloc[-1] > r.high.iloc[-2] >= r.high.iloc[-3] and r.low.iloc[-1] > r.low.iloc[-2] >= r.low.iloc[-3])
    return bool(r.high.iloc[-1] < r.high.iloc[-2] <= r.high.iloc[-3] and r.low.iloc[-1] < r.low.iloc[-2] <= r.low.iloc[-3])


def _trigger(day1: pd.DataFrame, now: datetime, side: str, gate: str, signal_high: float, signal_low: float, signal_close: float):
    future = day1[(day1.timestamp >= now) & (day1.timestamp < now + timedelta(minutes=7))].copy()
    if future.empty:
        return None
    prior = None
    pulled = False
    for row in future.itertuples(index=False):
        close_time = row.timestamp + timedelta(minutes=1)
        if side == "CE":
            adverse = signal_close - float(row.low)
            if 1.5 <= adverse <= 4.75:
                pulled = True
            if adverse > 4.75:
                return None
            if gate == "breakout" and float(row.close) > signal_high:
                return close_time
            if gate == "pullback_reclaim" and pulled and float(row.close) > signal_close + 0.5 and float(row.close) > float(row.open):
                return close_time
            if gate == "micro_break" and prior is not None and float(row.close) > float(prior.high) and float(row.close) > float(row.open):
                return close_time
        else:
            adverse = float(row.high) - signal_close
            if 1.5 <= adverse <= 4.75:
                pulled = True
            if adverse > 4.75:
                return None
            if gate == "breakout" and float(row.close) < signal_low:
                return close_time
            if gate == "pullback_reclaim" and pulled and float(row.close) < signal_close - 0.5 and float(row.close) < float(row.open):
                return close_time
            if gate == "micro_break" and prior is not None and float(row.close) < float(prior.low) and float(row.close) < float(row.open):
                return close_time
        prior = row
    return None


def _outcome(day1: pd.DataFrame, entry_time: datetime, side: str):
    prior = day1[day1.timestamp + timedelta(minutes=1) <= entry_time]
    if prior.empty:
        return None
    entry = float(prior.iloc[-1].close)
    future = day1[(day1.timestamp + timedelta(minutes=1) > entry_time) & (day1.timestamp + timedelta(minutes=1) <= entry_time + timedelta(minutes=HORIZON_MINUTES))]
    if future.empty:
        return None
    mfe = 0.0
    mae = 0.0
    result = "TIMEOUT"
    for row in future.itertuples(index=False):
        if side == "CE":
            favorable = float(row.high) - entry
            adverse = entry - float(row.low)
        else:
            favorable = entry - float(row.low)
            adverse = float(row.high) - entry
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)
        hit_t = favorable >= TARGET_POINTS
        hit_s = adverse >= STOP_POINTS
        if hit_s:
            result = "LOSS"  # conservative if both occur in the same 1m candle
            break
        if hit_t:
            result = "WIN"
            break
    return {"entry": entry, "mfe": mfe, "mae": mae, "result": result}


def build_rows(start: date, end: date, cache: Path):
    spot, options, loaded = load_frames(start, end, cache)
    rows = []
    tested_days = []
    skipped_days = []
    for day in sorted(set(spot.day)):
        expiries = sorted(e for e in set(options.expiry) if e >= day)
        if not expiries or (expiries[0] - day).days > MAX_DTE:
            skipped_days.append(day.isoformat())
            continue
        expiry = expiries[0]
        day1 = spot[spot.day == day].copy().sort_values("timestamp")
        dayopt = options[(options.day == day) & (options.expiry == expiry)].copy()
        if day1.empty or dayopt.empty:
            skipped_days.append(day.isoformat())
            continue
        tf3 = aggregate(day1, 3)
        tf5 = aggregate(day1, 5)
        tf15 = aggregate(day1, 15)
        option5 = {}
        first_seen = {}
        for (strike, side), group in dayopt.groupby(["strike", "side"], sort=False):
            key = (int(strike), str(side))
            option5[key] = aggregate(group, 5)
            first_seen[key] = group.timestamp.min()
        common = sorted(s for s in {k[0] for k in option5} if (s, "CE") in option5 and (s, "PE") in option5)
        if len(common) < 5:
            skipped_days.append(day.isoformat())
            continue
        tested_days.append(day.isoformat())
        now = datetime.combine(day, time(9, 35))
        end_ts = datetime.combine(day, time(14, 45))
        while now <= end_ts:
            s3 = completed(tf3, now, 3)
            s5 = completed(tf5, now, 5)
            s15 = completed(tf15, now, 15)
            if len(s5) < 6 or len(s15) < 3 or len(s3) < 4:
                now += timedelta(minutes=5)
                continue
            spot_now = float(s5.iloc[-1].close)
            visible = [s for s in common if first_seen[(s, "CE")] < now and first_seen[(s, "PE")] < now]
            if len(visible) < 5:
                now += timedelta(minutes=5)
                continue
            ai = min(range(len(visible)), key=lambda i: abs(visible[i] - spot_now))
            if ai < 2 or ai + 2 >= len(visible):
                now += timedelta(minutes=5)
                continue
            strikes = visible[ai-2:ai+3]
            weights = [1.0, 2.0, 3.0, 2.0, 1.0]
            atm = strikes[2]
            signal = s5.iloc[-1]
            impulse_abs = abs(float(s5.close.iloc[-1] - s5.close.iloc[-3]))
            eff = _efficiency(s5.close, 5)
            close_pos = 0.5 if signal.high <= signal.low else float((signal.close - signal.low) / (signal.high - signal.low))

            for side in ("CE", "PE"):
                other = "PE" if side == "CE" else "CE"
                direction = 1.0 if side == "CE" else -1.0
                impulse = direction * float(s5.close.iloc[-1] - s5.close.iloc[-3])
                struct15 = _structure(s15, side)
                struct3 = _structure(s3, side)
                position_ok = close_pos >= 0.62 if side == "CE" else close_pos <= 0.38
                chosen_moves = []
                other_moves = []
                breadth_num = 0.0
                breadth_den = 0.0
                chosen_oi = []
                other_oi = []
                vol_ratios = []
                enough = True
                for strike, weight in zip(strikes, weights):
                    chosen = completed(option5[(strike, side)], now, 5)
                    opposite = completed(option5[(strike, other)], now, 5)
                    if len(chosen) < 4 or len(opposite) < 4:
                        enough = False
                        break
                    cm = _pct_move(chosen.close, 3)
                    om = _pct_move(opposite.close, 3)
                    chosen_moves.append((weight, cm)); other_moves.append((weight, om))
                    breadth_den += weight
                    if cm > 0 and om < cm:
                        breadth_num += weight
                    c_oi0, c_oi1 = float(chosen.oi.iloc[-4]), float(chosen.oi.iloc[-1])
                    o_oi0, o_oi1 = float(opposite.oi.iloc[-4]), float(opposite.oi.iloc[-1])
                    chosen_oi.append((weight, c_oi1 - c_oi0)); other_oi.append((weight, o_oi1 - o_oi0))
                    prev_vol = chosen.volume.iloc[-4:-1].astype(float)
                    med = float(prev_vol.median()) if len(prev_vol) else 0.0
                    vol_ratios.append(float(chosen.volume.iloc[-1]) / med if med > 0 else 1.0)
                if not enough:
                    continue
                wsum = sum(w for w, _ in chosen_moves)
                chosen_move = sum(w * v for w, v in chosen_moves) / wsum
                other_move = sum(w * v for w, v in other_moves) / wsum
                dominance = 100.0 * breadth_num / breadth_den if breadth_den else 0.0
                atm_chosen = completed(option5[(atm, side)], now, 5)
                atm_other = completed(option5[(atm, other)], now, 5)
                atm_move = _pct_move(atm_chosen.close, 3)
                atm_edge = atm_move - _pct_move(atm_other.close, 3)
                chosen_oi_delta = sum(w * v for w, v in chosen_oi) / wsum
                other_oi_delta = sum(w * v for w, v in other_oi) / wsum
                # Directional option-flow support: selected premium rising, opposite premium lagging,
                # plus either opposite-side OI build or selected-side OI unwind.
                oi_support = bool(other_oi_delta > 0 or chosen_oi_delta < 0)
                vol_ratio = float(np.median(vol_ratios)) if vol_ratios else 1.0

                for gate in GATES:
                    entry_time = _trigger(day1, now, side, gate, float(signal.high), float(signal.low), float(signal.close))
                    if entry_time is None:
                        continue
                    outcome = _outcome(day1, entry_time, side)
                    if outcome is None:
                        continue
                    minute = now.hour * 60 + now.minute
                    rows.append({
                        "timestamp": now,
                        "entry_time": entry_time,
                        "day": day,
                        "month": day.month,
                        "side": side,
                        "gate": gate,
                        "impulse": impulse,
                        "impulse_abs": impulse_abs,
                        "efficiency": eff,
                        "structure15": struct15,
                        "structure3": struct3,
                        "position_ok": position_ok,
                        "atm_move": atm_move,
                        "atm_edge": atm_edge,
                        "weighted_move": chosen_move,
                        "dominance": dominance,
                        "oi_support": oi_support,
                        "volume_ratio": vol_ratio,
                        "minute": minute,
                        **outcome,
                    })
            now += timedelta(minutes=5)
    return pd.DataFrame(rows), loaded, tested_days, skipped_days


def _dedup(sample: pd.DataFrame, cooldown: int = 20) -> pd.DataFrame:
    if sample.empty:
        return sample
    keep = []
    last = {}
    for idx, row in sample.sort_values("entry_time").iterrows():
        key = (row.day, row.side)
        prev = last.get(key)
        if prev is None or row.entry_time - prev >= timedelta(minutes=cooldown):
            keep.append(idx)
            last[key] = row.entry_time
    return sample.loc[keep]


def _score(sample: pd.DataFrame):
    s = _dedup(sample)
    n = len(s)
    wins = int((s.result == "WIN").sum()) if n else 0
    losses = int((s.result == "LOSS").sum()) if n else 0
    return n, wins, losses, round(100.0 * wins / n, 2) if n else 0.0


def _time_mask(df: pd.DataFrame, mode: str):
    if mode == "MORNING":
        return (df.minute >= 585) & (df.minute <= 690)  # 09:45-11:30
    if mode == "MIDDAY":
        return (df.minute >= 690) & (df.minute <= 810)  # 11:30-13:30
    if mode == "AFTERNOON":
        return (df.minute >= 810) & (df.minute <= 885)  # 13:30-14:45
    return pd.Series(True, index=df.index)


def apply_rule(rows: pd.DataFrame, rule: dict) -> pd.DataFrame:
    m = (
        (rows.gate == rule["gate"])
        & (rows.impulse >= rule["impulse_min"])
        & (rows.efficiency >= rule["efficiency_min"])
        & (rows.atm_move >= rule["atm_move_min"])
        & (rows.atm_edge >= rule["atm_edge_min"])
        & (rows.dominance >= rule["dominance_min"])
        & (rows.volume_ratio >= rule["volume_ratio_min"])
        & _time_mask(rows, rule["time_mode"])
    )
    if rule["side"] != "BOTH":
        m &= rows.side == rule["side"]
    if rule["structure15"]:
        m &= rows.structure15
    if rule["structure3"]:
        m &= rows.structure3
    if rule["position_required"]:
        m &= rows.position_ok
    if rule["oi_required"]:
        m &= rows.oi_support
    return _dedup(rows[m])


def candidate_rules(rows: pd.DataFrame):
    dev = rows[rows.month.isin([1, 2])]
    val = rows[rows.month.isin([3, 4])]
    candidates = []
    # Controlled family. May-June is never used for rule selection.
    for side, gate, impulse_min, eff_min, atm_min, edge_min, dom_min, struct3, pos_req, oi_req, vol_min, time_mode in product(
        ("CE", "PE", "BOTH"),
        GATES,
        (4.0, 8.0, 12.0),
        (0.35, 0.55),
        (0.5, 1.0, 1.5),
        (0.5, 1.5),
        (65.0, 75.0, 85.0),
        (False, True),
        (False, True),
        (False, True),
        (0.9, 1.2),
        ("ALL", "MORNING", "MIDDAY", "AFTERNOON"),
    ):
        rule = {
            "side": side,
            "gate": gate,
            "impulse_min": impulse_min,
            "efficiency_min": eff_min,
            "atm_move_min": atm_min,
            "atm_edge_min": edge_min,
            "dominance_min": dom_min,
            "structure15": True,
            "structure3": struct3,
            "position_required": pos_req,
            "oi_required": oi_req,
            "volume_ratio_min": vol_min,
            "time_mode": time_mode,
        }
        dn, dw, dl, dr = _score(apply_rule(dev, rule))
        vn, vw, vl, vr = _score(apply_rule(val, rule))
        if dn < 10 or vn < 12:
            continue
        robust = min(dr, vr)
        candidates.append({**rule, "dev_trades": dn, "dev_wins": dw, "dev_win_rate": dr, "validation_trades": vn, "validation_wins": vw, "validation_win_rate": vr, "robust_rate": robust})
    candidates.sort(key=lambda x: (x["robust_rate"], min(x["dev_trades"], x["validation_trades"]), x["validation_win_rate"]), reverse=True)
    return candidates


def max_losing_streak(sample: pd.DataFrame) -> int:
    streak = best = 0
    for result in sample.sort_values("entry_time").result.tolist():
        if result == "WIN":
            streak = 0
        else:
            streak += 1
            best = max(best, streak)
    return best


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--output", default="shiv_precision_search.json")
    p.add_argument("--cache", default=".cache/shiv-precision")
    a = p.parse_args()
    rows, loaded, tested, skipped = build_rows(a.start, a.end, Path(a.cache))
    candidates = candidate_rules(rows)
    best = candidates[0] if candidates else None
    final_rows = rows[rows.month.isin([5, 6])]
    payload = {
        "strategy": "SHIV PRECISION RECLAIM V1 research",
        "protocol": "Jan-Feb development; Mar-Apr validation; best rule locked before May-Jun final test. May-Jun outcomes never select thresholds.",
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop counts as LOSS",
        "source": HF_REPO,
        "source_license": "CC-BY-NC-4.0",
        "tested_days": len(tested),
        "loaded_expiry_files": loaded,
        "skipped_days": skipped,
        "candidate_count": len(candidates),
        "best_rule": best,
        "top_20_rules": candidates[:20],
    }
    if best:
        test = apply_rule(final_rows, best)
        n, wins, losses, rate = _score(test)
        payload["may_june_final_test"] = {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": rate,
            "max_losing_streak": max_losing_streak(test),
            "median_mfe": round(float(test.mfe.median()), 2) if n else 0.0,
            "median_mae": round(float(test.mae.median()), 2) if n else 0.0,
            "signals": [
                {"timestamp": r.timestamp.isoformat(), "entry_time": r.entry_time.isoformat(), "side": r.side, "gate": r.gate, "result": r.result, "mfe": round(float(r.mfe), 2), "mae": round(float(r.mae), 2)}
                for r in test.itertuples()
            ],
        }
        payload["accept_for_paper_live"] = bool(n >= 20 and rate >= 70.0 and max_losing_streak(test) <= 3)
        payload["target_band_met"] = bool(n >= 20 and 70.0 <= rate <= 90.0)
    else:
        payload["may_june_final_test"] = {"trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0}
        payload["accept_for_paper_live"] = False
        payload["target_band_met"] = False
    Path(a.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
