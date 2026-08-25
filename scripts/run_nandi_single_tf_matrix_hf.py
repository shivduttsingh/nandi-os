from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.strike_window_strategy import StrikeWindowSignal, assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_shiv_aplus_hf_backtest import load_hf_frames

TIMEFRAMES = (1, 3, 5, 15)
MAX_DTE = 7
SYSTEM_ATM = "NANDI_ATM"
SYSTEM_WINDOW = "NANDI_ATM_PLUS_MINUS_2"


def _completed(series, now: datetime, tf: int):
    return tuple(c for c in series if c.timestamp + timedelta(minutes=tf) <= now)


def _rate(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else 0.0


def _outcome(day_spot, now: datetime, side: str):
    mfe, mae, m5, m10, m15, result, entry = base.outcome(day_spot, now, side)
    return {
        "entry": round(entry, 2),
        "mfe": round(mfe, 2),
        "mae": round(mae, 2),
        "m5": round(m5, 2),
        "m10": round(m10, 2),
        "m15": round(m15, 2),
        "result": result,
    }


def run(start: date, end: date, cache: Path):
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    stats = defaultdict(lambda: {
        "evaluations": 0, "signals": 0, "wins": 0, "losses": 0, "timeouts": 0,
        "ce_signals": 0, "ce_wins": 0, "pe_signals": 0, "pe_wins": 0,
        "mfe5": 0, "mfe10": 0, "mfe15": 0, "mfe20": 0,
        "cont5": 0, "cont10": 0, "cont15": 0,
    })
    signals = []
    tested_days = set()
    skipped = []

    for day in sorted(d for d in set(spot_df["day"]) if start <= d <= end):
        expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not expiries or (expiries[0] - day).days > MAX_DTE:
            skipped.append(day.isoformat())
            continue
        expiry = expiries[0]
        day_spot_df = spot_df[spot_df["day"] == day]
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            skipped.append(day.isoformat())
            continue

        day_spot = [base.candle_from(r) for r in day_spot_df.itertuples(index=False)]
        full_now = datetime.combine(day, time(15, 31))
        spot_by_tf = {tf: base.aggregate(day_spot, tf, full_now) for tf in TIMEFRAMES}

        raw_by_contract = {}
        first_seen = {}
        for (strike, side), group in day_opt.groupby(["strike", "side"], sort=False):
            raw = [base.candle_from(r) for r in group.sort_values("timestamp").itertuples(index=False)]
            if not raw:
                continue
            key = (int(strike), str(side))
            raw_by_contract[key] = raw
            first_seen[key] = raw[0].timestamp
        common = sorted(
            s for s in {k[0] for k in raw_by_contract}
            if (s, "CE") in raw_by_contract and (s, "PE") in raw_by_contract
        )
        if len(common) < 5:
            skipped.append(day.isoformat())
            continue
        option_by_tf = {
            tf: {key: base.aggregate(raw, tf, full_now) for key, raw in raw_by_contract.items()}
            for tf in TIMEFRAMES
        }
        tested_days.add(day)

        for tf in TIMEFRAMES:
            active_side = {SYSTEM_ATM: "", SYSTEM_WINDOW: ""}
            last_signal = {SYSTEM_ATM: None, SYSTEM_WINDOW: None}
            now = datetime.combine(day, time(9, 15)) + timedelta(minutes=tf)
            end_ts = datetime.combine(day, time(15, 15))
            while now <= end_ts:
                primary = _completed(spot_by_tf[tf], now, tf)
                if len(primary) < 5:
                    now += timedelta(minutes=tf)
                    continue
                visible = [
                    s for s in common
                    if first_seen.get((s, "CE"), now + timedelta(days=1)) < now
                    and first_seen.get((s, "PE"), now + timedelta(days=1)) < now
                ]
                if len(visible) < 5:
                    active_side[SYSTEM_ATM] = active_side[SYSTEM_WINDOW] = ""
                    now += timedelta(minutes=tf)
                    continue
                ai = min(range(len(visible)), key=lambda i: abs(visible[i] - primary[-1].close))
                if ai < 2 or ai + 2 >= len(visible):
                    active_side[SYSTEM_ATM] = active_side[SYSTEM_WINDOW] = ""
                    now += timedelta(minutes=tf)
                    continue
                strikes = visible[ai-2:ai+3]
                try:
                    window = tuple(
                        OptionStrikeCandles(
                            float(strike), expiry.isoformat(), offset,
                            _completed(option_by_tf[tf][(strike, "CE")], now, tf),
                            _completed(option_by_tf[tf][(strike, "PE")], now, tf),
                        )
                        for offset, strike in enumerate(strikes, start=-2)
                    )
                except KeyError:
                    now += timedelta(minutes=tf)
                    continue
                atm = window[2]
                if not atm.ce_candles or not atm.pe_candles:
                    now += timedelta(minutes=tf)
                    continue

                assessments = {}
                a = assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
                side = "CE" if a.signal == ATMConfirmationSignal.CONFIRM_CE else "PE" if a.signal == ATMConfirmationSignal.CONFIRM_PE else ""
                assessments[SYSTEM_ATM] = (side, float(a.agreement_score))

                if all(x.ce_candles and x.pe_candles for x in window):
                    w = assess_strike_window_confirmation(primary, window)
                    side = "CE" if w.signal == StrikeWindowSignal.CONFIRM_CE else "PE" if w.signal == StrikeWindowSignal.CONFIRM_PE else ""
                    assessments[SYSTEM_WINDOW] = (side, float(w.agreement_score))
                else:
                    assessments[SYSTEM_WINDOW] = ("", 0.0)

                for system, (side, score) in assessments.items():
                    key = (system, tf)
                    stats[key]["evaluations"] += 1
                    if side not in {"CE", "PE"}:
                        active_side[system] = ""
                        continue
                    # Collapse a continuous run of the same confirmation and also require
                    # at least 15 minutes between recorded signals on the same system/tf.
                    if active_side[system] == side:
                        continue
                    if last_signal[system] is not None and now - last_signal[system] < timedelta(minutes=15):
                        active_side[system] = side
                        continue
                    active_side[system] = side
                    last_signal[system] = now
                    out = _outcome(day_spot, now, side)
                    stats[key]["signals"] += 1
                    stats[key][f"{side.lower()}_signals"] += 1
                    if out["result"] == "WIN":
                        stats[key]["wins"] += 1
                        stats[key][f"{side.lower()}_wins"] += 1
                    elif out["result"] == "LOSS":
                        stats[key]["losses"] += 1
                    else:
                        stats[key]["timeouts"] += 1
                    stats[key]["mfe5"] += int(out["mfe"] >= 5)
                    stats[key]["mfe10"] += int(out["mfe"] >= 10)
                    stats[key]["mfe15"] += int(out["mfe"] >= 15)
                    stats[key]["mfe20"] += int(out["mfe"] >= 20)
                    stats[key]["cont5"] += int(out["m5"] > 0)
                    stats[key]["cont10"] += int(out["m10"] > 0)
                    stats[key]["cont15"] += int(out["m15"] > 0)
                    signals.append({"day": day.isoformat(), "timestamp": now.isoformat(), "system": system, "timeframe_min": tf, "side": side, "score": round(score,1), **out})
                now += timedelta(minutes=tf)

    matrix = {}
    for system in (SYSTEM_ATM, SYSTEM_WINDOW):
        matrix[system] = {}
        for tf in TIMEFRAMES:
            s = stats[(system, tf)]
            n = s["signals"]
            matrix[system][str(tf)] = {
                **s,
                "win_rate_pct": _rate(s["wins"], n),
                "loss_rate_pct": _rate(s["losses"], n),
                "ce_win_rate_pct": _rate(s["ce_wins"], s["ce_signals"]),
                "pe_win_rate_pct": _rate(s["pe_wins"], s["pe_signals"]),
                "mfe_5pt_hit_rate_pct": _rate(s["mfe5"], n),
                "mfe_10pt_hit_rate_pct": _rate(s["mfe10"], n),
                "mfe_15pt_hit_rate_pct": _rate(s["mfe15"], n),
                "mfe_20pt_hit_rate_pct": _rate(s["mfe20"], n),
                "continuation_5m_pct": _rate(s["cont5"], n),
                "continuation_10m_pct": _rate(s["cont10"], n),
                "continuation_15m_pct": _rate(s["cont15"], n),
            }
    return {
        "from_date": start.isoformat(), "to_date": end.isoformat(),
        "tested_days": len(tested_days), "skipped_days_without_near_expiry": skipped,
        "timeframes_minutes": list(TIMEFRAMES),
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop is a conservative LOSS",
        "methodology": "Each Nandi strategy is rerun independently on one timeframe at a time. NIFTY and option candles are aggregated from causal 1m bars to that timeframe. Outcome is always measured on subsequent 1m NIFTY candles.",
        "source": "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m",
        "loaded_expiry_files": loaded,
        "systems": matrix,
        "signals": signals,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--output", default="nandi_single_tf_matrix.json")
    p.add_argument("--cache", default=".cache/hf-india-options")
    a = p.parse_args()
    payload = run(a.start, a.end, Path(a.cache))
    Path(a.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
