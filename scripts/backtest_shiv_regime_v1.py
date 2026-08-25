from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

HF_REPO = "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m"
HF_RESOLVE = HF_REPO + "/resolve/main"
MAX_DTE = 7
TARGET = 10.0
STOP = 5.0
HORIZON = 15
MAX_TRADES_PER_DAY = 3
COOLDOWN_MINUTES = 20


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1000:
        return path
    req = Request(url, headers={"User-Agent": "Shiv-Regime-Research/1.0"})
    with urlopen(req, timeout=180) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    return path


def _ts(values: pd.Series) -> pd.Series:
    out = pd.to_datetime(values, errors="coerce")
    try:
        if out.dt.tz is not None:
            out = out.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return out


def load_frames(start: date, end: date, cache: Path):
    index_path = _download(HF_RESOLVE + "/index/NIFTY.parquet?download=true", cache / "NIFTY.parquet")
    idx = pd.read_parquet(index_path)
    spot = pd.DataFrame({
        "timestamp": _ts(idx["timestamp"]),
        "open": pd.to_numeric(idx["open"], errors="coerce"),
        "high": pd.to_numeric(idx["high"], errors="coerce"),
        "low": pd.to_numeric(idx["low"], errors="coerce"),
        "close": pd.to_numeric(idx["close"], errors="coerce"),
        "volume": pd.to_numeric(idx.get("volume", 0), errors="coerce").fillna(0),
    }).dropna(subset=["timestamp", "open", "high", "low", "close"])
    spot["day"] = spot.timestamp.dt.date
    spot = spot[(spot.day >= start) & (spot.day <= end)].sort_values("timestamp")

    expiries: list[date] = []
    cursor = start
    while cursor <= end + timedelta(days=7):
        if cursor.weekday() == 1:  # Tuesday weekly NIFTY regime in 2026
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
        ots = _ts(raw["timestamp"])
        side_source = raw["option_type"] if "option_type" in raw else raw.get("right", "")
        side = side_source.astype(str).str.upper().str.strip().replace({"CALL": "CE", "PUT": "PE", "C": "CE", "P": "PE"})
        exp = pd.to_datetime(raw["expiry"], errors="coerce").dt.date if "expiry" in raw else pd.Series([expiry] * len(raw))
        frame = pd.DataFrame({
            "timestamp": ots,
            "day": ots.dt.date,
            "expiry": exp,
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
        if frame.empty:
            continue
        frame["strike"] = frame.strike.astype(int)
        frames.append(frame)
        loaded.append(expiry.isoformat())
    if not frames:
        raise RuntimeError("No NIFTY option files available for requested range")
    options = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["timestamp", "expiry", "strike", "side"]).sort_values(["timestamp", "strike", "side"])
    return spot, options, loaded


def bucket_start(ts: datetime, minutes: int) -> datetime:
    anchor = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    elapsed = int((ts - anchor).total_seconds() // 60)
    return anchor + timedelta(minutes=(max(0, elapsed) // minutes) * minutes)


def aggregate(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy().sort_values("timestamp")
    work["bucket"] = work.timestamp.map(lambda x: bucket_start(x, minutes))
    grouped = work.groupby("bucket", sort=True)
    out = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index().rename(columns={"bucket": "timestamp"})
    if "oi" in work.columns:
        out["oi"] = grouped.oi.last().to_numpy()
    return out


def completed(df: pd.DataFrame, now: datetime, minutes: int) -> pd.DataFrame:
    return df[df.timestamp + pd.to_timedelta(minutes, unit="m") <= now]


def efficiency(closes: pd.Series, bars: int = 6) -> float:
    if len(closes) < bars:
        return 0.0
    x = closes.iloc[-bars:].astype(float).to_numpy()
    path = float(np.abs(np.diff(x)).sum())
    return 0.0 if path <= 1e-9 else float(abs(x[-1] - x[0]) / path)


def option_stats(window: list[tuple[int, pd.DataFrame, pd.DataFrame]], side: str, now: datetime):
    chosen = 0.0
    opposite = 0.0
    weight_sum = 0.0
    breadth = 0.0
    oi_support_points = 0
    vol_ratios = []
    weights = [1.0, 2.0, 3.0, 2.0, 1.0]
    for weight, (_, ce, pe) in zip(weights, window):
        c = completed(ce if side == "CE" else pe, now, 5)
        o = completed(pe if side == "CE" else ce, now, 5)
        if len(c) < 4 or len(o) < 4:
            return None
        c0, c1 = float(c.close.iloc[-3]), float(c.close.iloc[-1])
        o0, o1 = float(o.close.iloc[-3]), float(o.close.iloc[-1])
        cm = 0.0 if c0 <= 0 else 100.0 * (c1 / c0 - 1.0)
        om = 0.0 if o0 <= 0 else 100.0 * (o1 / o0 - 1.0)
        chosen += weight * cm
        opposite += weight * om
        weight_sum += weight
        if cm > om and cm > 0:
            breadth += weight
        c_oi = float(c.oi.iloc[-1] - c.oi.iloc[-3])
        o_oi = float(o.oi.iloc[-1] - o.oi.iloc[-3])
        # Support is either chosen-option short covering or opposite-option writing.
        if (c_oi <= 0 and cm > 0) or (o_oi >= 0 and om <= 0):
            oi_support_points += 1
        prev = c.volume.iloc[-4:-1].astype(float)
        med = float(prev.median()) if len(prev) else 0.0
        vol_ratios.append(float(c.volume.iloc[-1]) / med if med > 0 else 1.0)
    return {
        "chosen_move": chosen / weight_sum,
        "opposite_move": opposite / weight_sum,
        "edge": (chosen - opposite) / weight_sum,
        "breadth": breadth / weight_sum,
        "oi_support": oi_support_points >= 3,
        "volume_ratio": float(np.median(vol_ratios)) if vol_ratios else 1.0,
    }


def classify_regime(s5: pd.DataFrame, s15: pd.DataFrame):
    if len(s5) < 9 or len(s15) < 3:
        return None
    signal = s5.iloc[-1]
    prior5 = s5.iloc[-7:-1]
    eff = efficiency(s5.close, 6)
    up_break = float(signal.close) > float(prior5.high.tail(5).max()) + 0.25
    dn_break = float(signal.close) < float(prior5.low.tail(5).min()) - 0.25
    impulse = float(s5.close.iloc[-1] - s5.close.iloc[-4])
    trend15 = float(s15.close.iloc[-1] - s15.close.iloc[-3])
    if up_break and impulse >= 6.0 and eff >= 0.45 and trend15 > 0:
        return {"regime": "TREND", "side": "CE", "level": float(prior5.high.tail(5).max()), "range_low": None, "range_high": None}
    if dn_break and impulse <= -6.0 and eff >= 0.45 and trend15 < 0:
        return {"regime": "TREND", "side": "PE", "level": float(prior5.low.tail(5).min()), "range_low": None, "range_high": None}

    base = s5.iloc[-9:-1]
    rhi = float(base.high.max())
    rlo = float(base.low.min())
    width = rhi - rlo
    if not (12.0 <= width <= 60.0):
        return None
    reff = efficiency(base.close, 8)
    net = abs(float(base.close.iloc[-1] - base.close.iloc[0]))
    if reff > 0.38 or net > 0.45 * width:
        return None
    pos = (float(signal.close) - rlo) / width
    candle_range = max(float(signal.high - signal.low), 1e-9)
    lower_wick = (min(float(signal.open), float(signal.close)) - float(signal.low)) / candle_range
    upper_wick = (float(signal.high) - max(float(signal.open), float(signal.close))) / candle_range
    close_pos = (float(signal.close) - float(signal.low)) / candle_range
    if pos <= 0.24 and lower_wick >= 0.20 and close_pos >= 0.52:
        return {"regime": "RANGE", "side": "CE", "level": rlo, "range_low": rlo, "range_high": rhi}
    if pos >= 0.76 and upper_wick >= 0.20 and close_pos <= 0.48:
        return {"regime": "RANGE", "side": "PE", "level": rhi, "range_low": rlo, "range_high": rhi}
    return None


def entry_trigger(day1: pd.DataFrame, now: datetime, setup: dict, signal) -> datetime | None:
    side = setup["side"]
    regime = setup["regime"]
    future = day1[(day1.timestamp >= now) & (day1.timestamp < now + timedelta(minutes=7))]
    previous = None
    pulled = False
    for row in future.itertuples(index=False):
        close_time = row.timestamp + timedelta(minutes=1)
        if side == "CE":
            if regime == "TREND":
                if float(row.low) <= float(signal.close) and float(row.low) >= float(setup["level"]) - 3.0:
                    pulled = True
                if float(row.low) < float(setup["level"]) - 3.0:
                    return None
                if pulled and previous is not None and float(row.close) > float(previous.high) and float(row.close) > float(setup["level"]):
                    return close_time
            else:
                if float(row.low) < float(setup["range_low"]) - 2.5:
                    return None
                if previous is not None and float(row.close) > float(previous.high) and float(row.close) > float(signal.close):
                    return close_time
        else:
            if regime == "TREND":
                if float(row.high) >= float(signal.close) and float(row.high) <= float(setup["level"]) + 3.0:
                    pulled = True
                if float(row.high) > float(setup["level"]) + 3.0:
                    return None
                if pulled and previous is not None and float(row.close) < float(previous.low) and float(row.close) < float(setup["level"]):
                    return close_time
            else:
                if float(row.high) > float(setup["range_high"]) + 2.5:
                    return None
                if previous is not None and float(row.close) < float(previous.low) and float(row.close) < float(signal.close):
                    return close_time
        previous = row
    return None


def outcome(day1: pd.DataFrame, entry_time: datetime, side: str):
    prior = day1[day1.timestamp + timedelta(minutes=1) <= entry_time]
    if prior.empty:
        return None
    entry = float(prior.iloc[-1].close)
    future = day1[(day1.timestamp + timedelta(minutes=1) > entry_time) & (day1.timestamp + timedelta(minutes=1) <= entry_time + timedelta(minutes=HORIZON))]
    if future.empty:
        return None
    mfe = 0.0
    mae = 0.0
    final_move = 0.0
    result = "TIMEOUT"
    for row in future.itertuples(index=False):
        if side == "CE":
            favorable = float(row.high) - entry
            adverse = entry - float(row.low)
            final_move = float(row.close) - entry
        else:
            favorable = entry - float(row.low)
            adverse = float(row.high) - entry
            final_move = entry - float(row.close)
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)
        hit_t = favorable >= TARGET
        hit_s = adverse >= STOP
        if hit_s:
            result = "LOSS"  # conservative if target and stop occur in same 1m candle
            break
        if hit_t:
            result = "WIN"
            break
    return entry, mfe, mae, final_move, result


def summarize(rows: list[dict]):
    n = len(rows)
    wins = sum(r["result"] == "WIN" for r in rows)
    losses = sum(r["result"] == "LOSS" for r in rows)
    timeouts = n - wins - losses
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate_pct": round(100.0 * wins / n, 2) if n else 0.0,
        "mfe_5_hit_pct": round(100.0 * sum(r["mfe"] >= 5 for r in rows) / n, 2) if n else 0.0,
        "mfe_10_hit_pct": round(100.0 * sum(r["mfe"] >= 10 for r in rows) / n, 2) if n else 0.0,
        "avg_mfe": round(sum(r["mfe"] for r in rows) / n, 2) if n else 0.0,
        "avg_mae": round(sum(r["mae"] for r in rows) / n, 2) if n else 0.0,
    }


def run(start: date, end: date, cache: Path):
    spot, options, loaded = load_frames(start, end, cache)
    trades: list[dict] = []
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
        s5all = aggregate(day1, 5)
        s15all = aggregate(day1, 15)
        opt5: dict[tuple[int, str], pd.DataFrame] = {}
        first_seen: dict[tuple[int, str], datetime] = {}
        for (strike, side), group in dayopt.groupby(["strike", "side"], sort=False):
            key = (int(strike), str(side))
            opt5[key] = aggregate(group, 5)
            first_seen[key] = group.timestamp.min()
        common = sorted(s for s in {k[0] for k in opt5} if (s, "CE") in opt5 and (s, "PE") in opt5)
        if len(common) < 5:
            skipped_days.append(day.isoformat())
            continue
        tested_days.append(day.isoformat())
        last_trade_at: datetime | None = None
        day_trade_count = 0
        now = datetime.combine(day, time(10, 0))
        end_ts = datetime.combine(day, time(14, 45))
        while now <= end_ts and day_trade_count < MAX_TRADES_PER_DAY:
            if last_trade_at is not None and now - last_trade_at < timedelta(minutes=COOLDOWN_MINUTES):
                now += timedelta(minutes=5)
                continue
            s5 = completed(s5all, now, 5)
            s15 = completed(s15all, now, 15)
            setup = classify_regime(s5, s15)
            if setup is None:
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
            window = [(s, opt5[(s, "CE")], opt5[(s, "PE")]) for s in strikes]
            stats = option_stats(window, setup["side"], now)
            if stats is None:
                now += timedelta(minutes=5)
                continue
            if setup["regime"] == "TREND":
                confirmed = stats["chosen_move"] >= 0.50 and stats["edge"] >= 0.75 and stats["breadth"] >= 0.60 and stats["oi_support"]
            else:
                confirmed = stats["chosen_move"] >= 0.20 and stats["edge"] >= 0.50 and stats["breadth"] >= 0.60 and stats["oi_support"]
            if not confirmed:
                now += timedelta(minutes=5)
                continue
            signal = s5.iloc[-1]
            entry_time = entry_trigger(day1, now, setup, signal)
            if entry_time is None:
                now += timedelta(minutes=5)
                continue
            result = outcome(day1, entry_time, setup["side"])
            if result is None:
                now += timedelta(minutes=5)
                continue
            entry, mfe, mae, final_move, label = result
            row = {
                "day": day.isoformat(),
                "signal_time": now.isoformat(),
                "entry_time": entry_time.isoformat(),
                "regime": setup["regime"],
                "side": setup["side"],
                "entry": round(entry, 2),
                "mfe": round(mfe, 2),
                "mae": round(mae, 2),
                "final_move": round(final_move, 2),
                "result": label,
                "option_move": round(stats["chosen_move"], 2),
                "option_edge": round(stats["edge"], 2),
                "breadth": round(stats["breadth"], 2),
                "oi_support": bool(stats["oi_support"]),
                "volume_ratio": round(stats["volume_ratio"], 2),
            }
            trades.append(row)
            last_trade_at = entry_time
            day_trade_count += 1
            now = max(now + timedelta(minutes=5), entry_time)

    groups = {
        "overall": summarize(trades),
        "trend": summarize([r for r in trades if r["regime"] == "TREND"]),
        "range": summarize([r for r in trades if r["regime"] == "RANGE"]),
        "trend_ce": summarize([r for r in trades if r["regime"] == "TREND" and r["side"] == "CE"]),
        "trend_pe": summarize([r for r in trades if r["regime"] == "TREND" and r["side"] == "PE"]),
        "range_ce": summarize([r for r in trades if r["regime"] == "RANGE" and r["side"] == "CE"]),
        "range_pe": summarize([r for r in trades if r["regime"] == "RANGE" and r["side"] == "PE"]),
    }
    return {
        "strategy": "SHIV Regime V1 — Trend Pullback/Reclaim + Sideways Edge Reversal",
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop = conservative LOSS",
        "methodology": "5m classifies TREND/RANGE; 15m confirms trend direction; ATM±2 premium/OI confirms; 1m supplies post-signal entry trigger. Range entries only near range edges. Maximum 3 trades/day and 20-minute cooldown.",
        "source": HF_REPO,
        "loaded_expiry_files": loaded,
        "tested_days": len(tested_days),
        "tested_day_list": tested_days,
        "skipped_days": skipped_days,
        "summary": groups,
        "trades": trades,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--cache", default=".cache/hf-india-options")
    p.add_argument("--output", default="shiv_regime_v1_results.json")
    a = p.parse_args()
    payload = run(a.start, a.end, Path(a.cache))
    Path(a.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
