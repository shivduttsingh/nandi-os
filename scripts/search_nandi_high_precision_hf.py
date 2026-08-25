from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path

import pandas as pd

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.strike_window_strategy import StrikeWindowSignal, assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_shiv_aplus_hf_backtest import load_hf_frames

PRIMARY_INTERVAL = 5
MAX_DAYS_TO_EXPIRY = 7


def _completed(series, now: datetime):
    return tuple(c for c in series if c.timestamp + timedelta(minutes=PRIMARY_INTERVAL) <= now)


def _direction(signal) -> str:
    if signal in {ATMConfirmationSignal.CONFIRM_CE, StrikeWindowSignal.CONFIRM_CE}:
        return "CE"
    if signal in {ATMConfirmationSignal.CONFIRM_PE, StrikeWindowSignal.CONFIRM_PE}:
        return "PE"
    return ""


def _entry_time(day_spot, now: datetime, side: str, gate: str, signal_bar):
    if gate == "immediate":
        return now
    future = [c for c in day_spot if now <= c.timestamp < now + timedelta(minutes=5)]
    for c in future:
        close_time = c.timestamp + timedelta(minutes=1)
        if side == "CE":
            if gate == "body" and c.close > c.open and c.close > signal_bar.close:
                return close_time
            if gate == "breakout" and c.close > signal_bar.high:
                return close_time
        else:
            if gate == "body" and c.close < c.open and c.close < signal_bar.close:
                return close_time
            if gate == "breakout" and c.close < signal_bar.low:
                return close_time
    return None


def _outcome(day_spot, entry_time: datetime | None, side: str):
    if entry_time is None:
        return None
    mfe, mae, m5, m10, m15, result, entry = base.outcome(day_spot, entry_time, side)
    return {
        "entry_time": entry_time,
        "entry": entry,
        "mfe": mfe,
        "mae": mae,
        "m5": m5,
        "m10": m10,
        "m15": m15,
        "result": result,
    }


def build_rows(start: date, end: date, cache: Path):
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    rows = []
    tested_days = set()
    skipped_days = []
    for day in sorted(day for day in set(spot_df["day"]) if start <= day <= end):
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries or (future_expiries[0] - day).days > MAX_DAYS_TO_EXPIRY:
            skipped_days.append(day.isoformat()); continue
        expiry = future_expiries[0]
        day_spot_df = spot_df[spot_df["day"] == day]
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            skipped_days.append(day.isoformat()); continue
        day_spot = [base.candle_from(r) for r in day_spot_df.itertuples(index=False)]
        full_now = datetime.combine(day, time(15,31))
        spot5 = base.aggregate(day_spot, 5, full_now)
        option5, first_seen = {}, {}
        for (strike, side), group in day_opt.groupby(["strike","side"], sort=False):
            raw = [base.candle_from(r) for r in group.sort_values("timestamp").itertuples(index=False)]
            if not raw: continue
            key = (int(strike), str(side)); first_seen[key] = raw[0].timestamp
            option5[key] = base.aggregate(raw, 5, full_now)
        common = sorted(s for s in {k[0] for k in option5} if (s,"CE") in option5 and (s,"PE") in option5)
        if len(common) < 5:
            skipped_days.append(day.isoformat()); continue
        tested_days.add(day)
        now = datetime.combine(day, time(9,30))
        end_ts = datetime.combine(day, time(14,45))
        while now <= end_ts:
            primary = _completed(spot5, now)
            if len(primary) < 5:
                now += timedelta(minutes=5); continue
            visible = [s for s in common if first_seen.get((s,"CE"), now+timedelta(days=1)) < now and first_seen.get((s,"PE"), now+timedelta(days=1)) < now]
            if len(visible) < 5:
                now += timedelta(minutes=5); continue
            ai = min(range(len(visible)), key=lambda i: abs(visible[i]-primary[-1].close))
            if ai < 2 or ai+2 >= len(visible):
                now += timedelta(minutes=5); continue
            strikes = visible[ai-2:ai+3]
            window = tuple(OptionStrikeCandles(float(s), expiry.isoformat(), off, _completed(option5[(s,"CE")],now), _completed(option5[(s,"PE")],now)) for off,s in enumerate(strikes,start=-2))
            if any(not x.ce_candles or not x.pe_candles for x in window):
                now += timedelta(minutes=5); continue
            atm = assess_atm_confirmation(primary, window[2].ce_candles, window[2].pe_candles)
            win = assess_strike_window_confirmation(primary, window)
            atm_side = _direction(atm.signal); win_side = _direction(win.signal)
            for side in ("CE","PE"):
                aligned_structure = win.nifty_structure == ("BULLISH" if side=="CE" else "BEARISH")
                base_row = {
                    "timestamp": now, "day": day, "month": day.month, "side": side,
                    "atm_match": atm_side == side, "atm_score": float(atm.agreement_score if atm_side==side else 0.0),
                    "window_match": win_side == side, "window_score": float(win.agreement_score if win_side==side else 0.0),
                    "oi_supports": bool(win_side==side and win.oi_confirmation=="SUPPORTS"),
                    "volume_expanding": bool(win_side==side and win.volume_confirmation=="EXPANDING"),
                    "structure_aligned": bool(win_side==side and aligned_structure),
                    "trend_eff": float(win.trend_efficiency or 0.0),
                    "weighted_dom": float(win.weighted_dominance_pct if win_side==side else 0.0),
                    "persistence": int(win.persistence_bars if win_side==side else 0),
                    "minute": now.hour*60+now.minute,
                }
                for gate in ("immediate","body","breakout"):
                    et = _entry_time(day_spot, now, side, gate, primary[-1])
                    out = _outcome(day_spot, et, side)
                    if out is None: continue
                    rows.append({**base_row, "gate": gate, **out})
            now += timedelta(minutes=5)
    return pd.DataFrame(rows), loaded, sorted(tested_days), skipped_days


def _dedup(sample: pd.DataFrame, cooldown_min: int = 15) -> pd.DataFrame:
    if sample.empty: return sample
    sample = sample.sort_values("timestamp")
    keep=[]; last_by_side={}
    for idx,row in sample.iterrows():
        key=(row["day"],row["side"])
        last=last_by_side.get(key)
        if last is None or row["entry_time"]-last >= timedelta(minutes=cooldown_min):
            keep.append(idx); last_by_side[key]=row["entry_time"]
    return sample.loc[keep]


def _score(sample: pd.DataFrame):
    s=_dedup(sample)
    n=len(s); wins=int((s["result"]=="WIN").sum())
    return n, wins, round(100*wins/n,2) if n else 0.0


def select_rule(rows: pd.DataFrame):
    march=rows[rows["month"]==3]; april=rows[rows["month"]==4]
    candidates=[]
    for side_mode, atm_min, win_min, oi_req, vol_req, struct_req, eff_min, dom_min, gate in product(
        ("CE","PE","BOTH"), (80,90,95), (70,80,90), (False,True), (False,True), (False,True), (0.0,0.4,0.55), (70,80,90), ("immediate","body","breakout")
    ):
        def filt(df):
            m=(df.atm_match)&(df.window_match)&(df.atm_score>=atm_min)&(df.window_score>=win_min)&(df.weighted_dom>=dom_min)&(df.gate==gate)
            if side_mode!="BOTH": m &= df.side==side_mode
            if oi_req: m &= df.oi_supports
            if vol_req: m &= df.volume_expanding
            if struct_req: m &= df.structure_aligned
            if eff_min: m &= df.trend_eff>=eff_min
            return df[m]
        mn,mw,mr=_score(filt(march)); an,aw,ar=_score(filt(april))
        if mn < 8 or an < 8: continue
        robust=min(mr,ar)
        candidates.append({"side":side_mode,"atm_min":atm_min,"window_min":win_min,"oi_required":oi_req,"volume_required":vol_req,"structure_required":struct_req,"trend_eff_min":eff_min,"weighted_dom_min":dom_min,"gate":gate,"march_n":mn,"march_win_rate":mr,"april_n":an,"april_win_rate":ar,"robust_rate":robust,"total_dev_trades":mn+an})
    candidates.sort(key=lambda x:(x["robust_rate"], min(x["march_n"],x["april_n"]), x["total_dev_trades"]), reverse=True)
    return candidates


def apply_rule(rows: pd.DataFrame, rule: dict):
    m=(rows.atm_match)&(rows.window_match)&(rows.atm_score>=rule["atm_min"])&(rows.window_score>=rule["window_min"])&(rows.weighted_dom>=rule["weighted_dom_min"])&(rows.gate==rule["gate"])
    if rule["side"]!="BOTH": m &= rows.side==rule["side"]
    if rule["oi_required"]: m &= rows.oi_supports
    if rule["volume_required"]: m &= rows.volume_expanding
    if rule["structure_required"]: m &= rows.structure_aligned
    if rule["trend_eff_min"]: m &= rows.trend_eff>=rule["trend_eff_min"]
    return _dedup(rows[m])


def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",default="nandi_high_precision_search.json"); p.add_argument("--cache",default=".cache/hf-india-options")
    a=p.parse_args(); rows,loaded,tested,skipped=build_rows(date(2026,3,1),date(2026,6,30),Path(a.cache))
    dev=rows[rows.month.isin([3,4])]; final=rows[rows.month.isin([5,6])]
    candidates=select_rule(dev)
    best=candidates[0] if candidates else None
    payload={"protocol":"March development + April validation. Best rule then locked and applied to May-June final test. Search never optimises on May-June outcomes.","benchmark":"+10 NIFTY points before -5 within 15 minutes; same-candle target+stop = loss","tested_days":len(tested),"skipped_days":skipped,"loaded_expiry_files":loaded,"candidate_count":len(candidates),"best_development_rule":best,"top_10_development_rules":candidates[:10]}
    if best:
        test=apply_rule(final,best); n=len(test); wins=int((test.result=="WIN").sum())
        payload["may_june_final_test"]={"trades":n,"wins":wins,"losses":n-wins,"win_rate_pct":round(100*wins/n,2) if n else 0.0,"mfe_10_hit_pct":round(100*(test.mfe>=10).mean(),2) if n else 0.0,"continuation_5m_pct":round(100*(test.m5>0).mean(),2) if n else 0.0,"signals":[{"timestamp":r.timestamp.isoformat(),"entry_time":r.entry_time.isoformat(),"side":r.side,"gate":r.gate,"atm_score":round(r.atm_score,1),"window_score":round(r.window_score,1),"result":r.result} for r in test.itertuples()]}
        payload["accept_70pct"] = bool(n>=20 and wins/n>=0.70)
    else:
        payload["may_june_final_test"]={"trades":0,"wins":0,"losses":0,"win_rate_pct":0.0}; payload["accept_70pct"]=False
    Path(a.output).write_text(json.dumps(payload,indent=2),encoding="utf-8"); print(json.dumps(payload,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
