from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

from scripts import run_shiv_aplus_public_backtest as base

HF_REPO = "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m"
HF_RESOLVE = HF_REPO + "/resolve/main"


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1000:
        return path
    req = Request(url, headers={"User-Agent": "Shiv-Public-Backtest/1.0"})
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


def load_hf_frames(start: date, end: date, cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    index_path = _download(HF_RESOLVE + "/index/NIFTY.parquet?download=true", cache / "NIFTY.parquet")
    idx = pd.read_parquet(index_path)
    idx_ts = _normalize_timestamp(idx["timestamp"])
    spot = pd.DataFrame({
        "timestamp": idx_ts,
        "open": pd.to_numeric(idx["open"], errors="coerce"),
        "high": pd.to_numeric(idx["high"], errors="coerce"),
        "low": pd.to_numeric(idx["low"], errors="coerce"),
        "close": pd.to_numeric(idx["close"], errors="coerce"),
        "volume": pd.to_numeric(idx.get("volume", 0), errors="coerce").fillna(0),
        "oi": 0.0,
    }).dropna(subset=["timestamp", "open", "high", "low", "close"])
    spot["day"] = spot["timestamp"].dt.date
    spot = spot[(spot["day"] >= start) & (spot["day"] <= end)].sort_values("timestamp")

    expiries = []
    cursor = start
    while cursor <= end + timedelta(days=7):
        if cursor.weekday() == 1:  # Tuesday NIFTY weekly expiry regime in 2026.
            expiries.append(cursor)
        cursor += timedelta(days=1)
    # Include the first Tuesday at/after start and every Tuesday through one week beyond end.
    option_frames = []
    loaded = []
    for expiry in expiries:
        name = expiry.isoformat() + ".parquet"
        url = HF_RESOLVE + "/options/NIFTY/" + name + "?download=true"
        path = cache / "options" / name
        try:
            _download(url, path)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        raw = pd.read_parquet(path)
        if raw.empty:
            continue
        loaded.append(expiry.isoformat())
        ts = _normalize_timestamp(raw["timestamp"])
        side_source = raw["option_type"] if "option_type" in raw else raw.get("right", "")
        side = side_source.astype(str).str.upper().str.strip().replace({"CALL":"CE", "PUT":"PE", "C":"CE", "P":"PE"})
        expiry_col = pd.to_datetime(raw["expiry"], errors="coerce").dt.date if "expiry" in raw else pd.Series([expiry] * len(raw))
        frame = pd.DataFrame({
            "timestamp": ts,
            "day": ts.dt.date,
            "expiry": expiry_col,
            "strike": pd.to_numeric(raw["strike"], errors="coerce"),
            "side": side,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw.get("volume", 0), errors="coerce").fillna(0),
            "oi": pd.to_numeric(raw.get("open_interest", raw.get("oi", 0)), errors="coerce").fillna(0),
        }).dropna(subset=["timestamp", "expiry", "strike", "open", "high", "low", "close"])
        frame = frame[(frame["day"] >= start) & (frame["day"] <= end) & frame["side"].isin(["CE", "PE"])]
        frame["strike"] = frame["strike"].astype(int)
        option_frames.append(frame)
    if not option_frames:
        raise RuntimeError("No NIFTY option expiry files were available from the Hugging Face source for the requested window")
    options = pd.concat(option_frames, ignore_index=True).drop_duplicates(subset=["timestamp", "expiry", "strike", "side"]).sort_values(["timestamp", "strike", "side"])
    return spot, options, loaded


def run_hf(start: date, end: date, cache: Path) -> tuple[base.Report, list[str]]:
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    report = base.Report(start, end)
    days = sorted(day for day in set(spot_df["day"]) if start <= day <= end)
    for day in days:
        day_spot_df = spot_df[spot_df["day"] == day]
        # nearest unexpired weekly contract only
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries:
            continue
        nearest_expiry = future_expiries[0]
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == nearest_expiry)]
        if day_spot_df.empty or day_opt.empty:
            continue
        day_spot = [base.candle_from(row) for row in day_spot_df.itertuples(index=False)]
        report.tested_days.add(day)
        shiv_tracker, a4_tracker = base.Tracker(), base.Tracker()
        now = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))
        while now <= end_ts:
            completed_1m = tuple(c for c in day_spot if c.timestamp + timedelta(minutes=1) <= now)
            if not completed_1m:
                now += timedelta(minutes=5); continue
            spot_by_tf = {1: completed_1m, 3: base.aggregate(day_spot, 3, now), 5: base.aggregate(day_spot, 5, now), 15: base.aggregate(day_spot, 15, now)}
            chosen = base.choose_window(day_opt, completed_1m[-1].close, now)
            if chosen is None:
                report.missing_window_evals += 1
                now += timedelta(minutes=5); continue
            strikes, expiry = chosen
            window = base.build_window(day_opt, strikes, expiry, now)
            if any(not item.ce_candles or not item.pe_candles for item in window):
                report.missing_window_evals += 1
                now += timedelta(minutes=5); continue
            base.evaluate_system(system="SHIV_V2", perfect_spread=False, all_opt=opt_df, day_opt=day_opt, day_spot=day_spot, spot_by_tf=spot_by_tf, window=window, strikes=strikes, expiry=expiry, now=now, tracker=shiv_tracker, report=report)
            base.evaluate_system(system="A++++_PERFECT_SPREAD_UPPER_BOUND", perfect_spread=True, all_opt=opt_df, day_opt=day_opt, day_spot=day_spot, spot_by_tf=spot_by_tf, window=window, strikes=strikes, expiry=expiry, now=now, tracker=a4_tracker, report=report)
            now += timedelta(minutes=5)
    return report, loaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output", default="shiv_aplus_hf_backtest_results.json")
    parser.add_argument("--cache", default=".cache/hf-india-options")
    args = parser.parse_args()
    report, loaded = run_hf(args.start, args.end, Path(args.cache))
    payload = report.payload()
    payload["source"] = HF_REPO
    payload["source_license"] = "CC-BY-NC-4.0"
    payload["loaded_expiry_files"] = loaded
    payload["methodology"] = payload["methodology"] + " Full-chain source is the TradeMarkk/Hugging Face per-expiry NIFTY dataset."
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
