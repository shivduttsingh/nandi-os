from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

HF_REPO = "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m"
HF_RESOLVE = HF_REPO + "/resolve/main"
TARGET = 10.0
STOP = 5.0
HORIZON = 15
MAX_DTE = 7


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1000:
        return path
    req = Request(url, headers={"User-Agent": "Shiv-ORB-Research/1.0"})
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


def expected_expiries(start: date, end: date) -> list[date]:
    # NSE: contracts expiring on/before 2025-08-31 retained Thursday expiry;
    # contracts expiring on/after 2025-09-01 use Tuesday expiry.
    out = []
    cursor = start - timedelta(days=7)
    last = end + timedelta(days=7)
    while cursor <= last:
        if cursor <= date(2025, 8, 31) and cursor.weekday() == 3:
            out.append(cursor)
        elif cursor >= date(2025, 9, 1) and cursor.weekday() == 1:
            out.append(cursor)
        cursor += timedelta(days=1)
    return sorted(set(out))


def load_frames(start: date, end: date, cache: Path):
    ipath = _download(HF_RESOLVE + "/index/NIFTY.parquet?download=true", cache / "NIFTY.parquet")
    idx = pd.read_parquet(ipath)
    t = _ts(idx["timestamp"])
    spot = pd.DataFrame({
        "timestamp": t,
        "open": pd.to_numeric(idx["open"], errors="coerce"),
        "high": pd.to_numeric(idx["high"], errors="coerce"),
        "low": pd.to_numeric(idx["low"], errors="coerce"),
        "close": pd.to_numeric(idx["close"], errors="coerce"),
        "volume": pd.to_numeric(idx.get("volume", 0), errors="coerce").fillna(0),
    }).dropna(subset=["timestamp", "open", "high", "low", "close"])
    spot["day"] = spot.timestamp.dt.date
    spot = spot[(spot.day >= start) & (spot.day <= end)].sort_values("timestamp")

    frames = []
    loaded = []
    for expiry in expected_expiries(start, end):
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
        ot = _ts(raw["timestamp"])
        ss = raw["option_type"] if "option_type" in raw else raw.get("right", "")
        side = ss.astype(str).str.upper().str.strip().replace({"CALL":"CE", "PUT":"PE", "C":"CE", "P":"PE"})
        exp = pd.to_datetime(raw["expiry"], errors="coerce").dt.date if "expiry" in raw else pd.Series([expiry]*len(raw))
        f = pd.DataFrame({
            "timestamp": ot,
            "day": ot.dt.date,
            "expiry": exp,
            "strike": pd.to_numeric(raw["strike"], errors="coerce"),
            "side": side,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw.get("volume",0), errors="coerce").fillna(0),
            "oi": pd.to_numeric(raw.get("open_interest",raw.get("oi",0)), errors="coerce").fillna(0),
        }).dropna(subset=["timestamp","expiry","strike","open","high","low","close"])
        f = f[(f.day >= start) & (f.day <= end) & f.side.isin(["CE","PE"])]
        f["strike"] = f.strike.astype(int)
        frames.append(f)
    if not frames:
        raise RuntimeError("No 2025 option files found")
    options = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["timestamp","expiry","strike","side"]).sort_values(["timestamp","strike","side"])
    return spot, options, loaded


def aggregate(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy().sort_values("timestamp")
    anchor = work.timestamp.dt.normalize() + pd.Timedelta(hours=9, minutes=15)
    elapsed = ((work.timestamp - anchor).dt.total_seconds() // 60).clip(lower=0).astype(int)
    work["bucket"] = anchor + pd.to_timedelta((elapsed // minutes) * minutes, unit="m")
    g = work.groupby("bucket", sort=True)
    out = g.agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")).reset_index().rename(columns={"bucket":"timestamp"})
    if "oi" in work:
        out["oi"] = g.oi.last().to_numpy()
    return out


def completed(df: pd.DataFrame, now: datetime, tf: int):
    return df[df.timestamp + pd.to_timedelta(tf,unit="m") <= now]


def pct_move(series: pd.Series, bars: int = 2) -> float:
    if len(series) <= bars:
        return 0.0
    a=float(series.iloc[-bars-1]); b=float(series.iloc[-1])
    return 0.0 if a <= 0 else 100.0*(b/a-1.0)


def efficiency(closes: pd.Series, bars: int = 4) -> float:
    if len(closes) < bars+1:
        return 0.0
    x=closes.iloc[-bars-1:].astype(float).to_numpy(); d=float(np.abs(np.diff(x)).sum())
    return 0.0 if d <= 1e-9 else float(abs(x[-1]-x[0])/d)


def outcome(day1: pd.DataFrame, entry_time: datetime, side: str):
    prev=day1[day1.timestamp + timedelta(minutes=1) <= entry_time]
    if prev.empty: return None
    entry=float(prev.iloc[-1].close)
    future=day1[(day1.timestamp + timedelta(minutes=1) > entry_time)&(day1.timestamp + timedelta(minutes=1) <= entry_time+timedelta(minutes=HORIZON))]
    if future.empty: return None
    mfe=mae=0.0; result="TIMEOUT"
    for r in future.itertuples(index=False):
        fav=float(r.high)-entry if side=="CE" else entry-float(r.low)
        adv=entry-float(r.low) if side=="CE" else float(r.high)-entry
        mfe=max(mfe,fav); mae=max(mae,adv)
        if adv >= STOP: result="LOSS"; break
        if fav >= TARGET: result="WIN"; break
    return {"entry":entry,"mfe":mfe,"mae":mae,"result":result}


def option_evidence(option5, first_seen, common, spot_now: float, now: datetime, side: str):
    visible=[s for s in common if first_seen.get((s,"CE"),now+timedelta(days=1)) < now and first_seen.get((s,"PE"),now+timedelta(days=1)) < now]
    if len(visible)<5: return None
    ai=min(range(len(visible)),key=lambda i:abs(visible[i]-spot_now))
    if ai<2 or ai+2>=len(visible): return None
    strikes=visible[ai-2:ai+3]; weights=[1.,2.,3.,2.,1.]
    other="PE" if side=="CE" else "CE"
    chosen=[]; opp=[]; breadth=0.; den=0.; oi_ch=[]; oi_op=[]; vr=[]
    for strike,w in zip(strikes,weights):
        c=completed(option5[(strike,side)],now,5); o=completed(option5[(strike,other)],now,5)
        if len(c)<3 or len(o)<3: return None
        cm=pct_move(c.close,2); om=pct_move(o.close,2)
        chosen.append((w,cm)); opp.append((w,om)); den+=w
        if cm>0 and cm>om: breadth+=w
        oi_ch.append((w,float(c.oi.iloc[-1]-c.oi.iloc[-3]))); oi_op.append((w,float(o.oi.iloc[-1]-o.oi.iloc[-3])))
        med=float(c.volume.iloc[-3:-1].astype(float).median())
        vr.append(float(c.volume.iloc[-1])/med if med>0 else 1.0)
    wsum=sum(w for w,_ in chosen)
    cm=sum(w*v for w,v in chosen)/wsum; om=sum(w*v for w,v in opp)/wsum
    c_oi=sum(w*v for w,v in oi_ch)/wsum; o_oi=sum(w*v for w,v in oi_op)/wsum
    atm=strikes[2]
    ac=completed(option5[(atm,side)],now,5); ao=completed(option5[(atm,other)],now,5)
    return {
        "atm_move": pct_move(ac.close,2),
        "atm_edge": pct_move(ac.close,2)-pct_move(ao.close,2),
        "weighted_move": cm,
        "dominance":100.0*breadth/den,
        "oi_support":bool(o_oi>0 or c_oi<0),
        "volume_ratio":float(np.median(vr)),
    }


def retest_trigger(day1: pd.DataFrame, breakout_time: datetime, side: str, level: float, depth: float, expiry_min: int=12):
    future=day1[(day1.timestamp >= breakout_time)&(day1.timestamp < breakout_time+timedelta(minutes=expiry_min))]
    touched=False; prior=None
    for r in future.itertuples(index=False):
        close_time=r.timestamp+timedelta(minutes=1)
        if side=="CE":
            if float(r.low) <= level+depth: touched=True
            if float(r.close) < level-depth: return None
            if touched and float(r.close)>level+0.5 and float(r.close)>float(r.open):
                if prior is None or float(r.close)>float(prior.high): return close_time
        else:
            if float(r.high) >= level-depth: touched=True
            if float(r.close) > level+depth: return None
            if touched and float(r.close)<level-0.5 and float(r.close)<float(r.open):
                if prior is None or float(r.close)<float(prior.low): return close_time
        prior=r
    return None


def build_candidates(start: date,end: date,cache: Path):
    spot,options,loaded=load_frames(start,end,cache)
    rows=[]; tested=[]; skipped=[]
    for day in sorted(set(spot.day)):
        exps=sorted(e for e in set(options.expiry) if e>=day)
        if not exps or (exps[0]-day).days>MAX_DTE: skipped.append(day.isoformat()); continue
        exp=exps[0]; day1=spot[spot.day==day].copy().sort_values("timestamp"); dayopt=options[(options.day==day)&(options.expiry==exp)]
        if day1.empty or dayopt.empty: skipped.append(day.isoformat()); continue
        opening=day1[(day1.timestamp>=datetime.combine(day,time(9,15)))&(day1.timestamp<datetime.combine(day,time(9,45)))]
        if len(opening)<20: skipped.append(day.isoformat()); continue
        or_high=float(opening.high.max()); or_low=float(opening.low.min()); or_width=or_high-or_low
        tf5=aggregate(day1,5); tf15=aggregate(day1,15)
        option5={}; first={}
        for (strike,side),g in dayopt.groupby(["strike","side"],sort=False):
            key=(int(strike),str(side)); option5[key]=aggregate(g,5); first[key]=g.timestamp.min()
        common=sorted(s for s in {k[0] for k in option5} if (s,"CE") in option5 and (s,"PE") in option5)
        if len(common)<5: skipped.append(day.isoformat()); continue
        tested.append(day.isoformat())
        # Generate one prospective breakout candidate per side; final rule chooses evidence thresholds.
        for side in ("CE","PE"):
            breakout=None
            for now in pd.date_range(datetime.combine(day,time(9,50)),datetime.combine(day,time(14,15)),freq="5min").to_pydatetime():
                s5=completed(tf5,now,5); s15=completed(tf15,now,15)
                if len(s5)<4 or len(s15)<3: continue
                last=s5.iloc[-1]
                level=or_high if side=="CE" else or_low
                dist=float(last.close-level) if side=="CE" else float(level-last.close)
                if dist < 0: continue
                directional=(float(last.close)>float(last.open)) if side=="CE" else (float(last.close)<float(last.open))
                if not directional: continue
                # 15m regime must already be directionally positive/negative.
                slope=float(s15.close.iloc[-1]-s15.close.iloc[-3])*(1 if side=="CE" else -1)
                if slope <= 0: continue
                ev=option_evidence(option5,first,common,float(last.close),now,side)
                if ev is None: continue
                breakout={"now":now,"last":last,"s5":s5,"s15":s15,"level":level,"distance":dist,"ev":ev}
                break
            if breakout is None: continue
            for depth in (2.5,4.5):
                et=retest_trigger(day1,breakout["now"],side,breakout["level"],depth)
                if et is None: continue
                out=outcome(day1,et,side)
                if out is None: continue
                s5=breakout["s5"]; s15=breakout["s15"]; ev=breakout["ev"]
                rows.append({
                    "day":day,"month":day.month,"side":side,"breakout_time":breakout["now"],"entry_time":et,
                    "or_width":or_width,"breakout_distance":breakout["distance"],"retest_depth":depth,
                    "eff5":efficiency(s5.close,4),"trend15":abs(float(s15.close.iloc[-1]-s15.close.iloc[-3])),
                    **ev,**out,
                })
    return pd.DataFrame(rows),loaded,tested,skipped


def dedup(df: pd.DataFrame):
    if df.empty:return df
    return df.sort_values("entry_time").drop_duplicates(subset=["day"],keep="first")


def score(df: pd.DataFrame):
    s=dedup(df); n=len(s); w=int((s.result=="WIN").sum()) if n else 0
    return n,w,n-w,round(100*w/n,2) if n else 0.0


def apply_rule(rows: pd.DataFrame,r: dict):
    m=(rows.breakout_distance>=r["breakout_min"])&(rows.breakout_distance<=r["breakout_max"])&(rows.or_width<=r["or_width_max"])&(rows.eff5>=r["eff_min"])&(rows.atm_move>=r["atm_min"])&(rows.atm_edge>=r["edge_min"])&(rows.dominance>=r["dom_min"])&(rows.volume_ratio>=r["vol_min"])&(rows.retest_depth==r["retest_depth"])
    if r["side"]!="BOTH":m &= rows.side==r["side"]
    if r["oi_required"]:m &= rows.oi_support
    return dedup(rows[m])


def select(rows: pd.DataFrame):
    dev=rows[rows.month.isin([1,2,3,4])]; val=rows[rows.month.isin([5,6,7,8])]
    c=[]
    # Small fixed family; Sep-Dec is never inspected during selection.
    for side,bmin,bmax,ow,eff,atm,edge,dom,oi,vol,depth in product(
        ("CE","PE","BOTH"),(0.,3.),(12.,20.),(90.,140.),(.35,.55),(.3,.8),(.5,1.5),(65.,75.),(False,True),(.9,1.2),(2.5,4.5)
    ):
        r={"side":side,"breakout_min":bmin,"breakout_max":bmax,"or_width_max":ow,"eff_min":eff,"atm_min":atm,"edge_min":edge,"dom_min":dom,"oi_required":oi,"vol_min":vol,"retest_depth":depth}
        dn,dw,dl,dr=score(apply_rule(dev,r)); vn,vw,vl,vr=score(apply_rule(val,r))
        if dn<12 or vn<12:continue
        c.append({**r,"dev_trades":dn,"dev_wins":dw,"dev_rate":dr,"validation_trades":vn,"validation_wins":vw,"validation_rate":vr,"robust_rate":min(dr,vr)})
    c.sort(key=lambda x:(x["robust_rate"],min(x["dev_trades"],x["validation_trades"]),x["validation_rate"]),reverse=True)
    return c


def max_losing_streak(df: pd.DataFrame):
    s=best=0
    for x in df.sort_values("entry_time").result.tolist():
        if x=="WIN":s=0
        else:s+=1;best=max(best,s)
    return best


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",default="shiv_orb_precision_2025.json");p.add_argument("--cache",default=".cache/shiv-orb-2025")
    a=p.parse_args();rows,loaded,tested,skipped=build_candidates(date(2025,1,1),date(2025,12,31),Path(a.cache));rules=select(rows);best=rules[0] if rules else None
    payload={"strategy":"SHIV ORB RETEST PRECISION research","protocol":"Jan-Apr 2025 development; May-Aug validation; selected rule locked before Sep-Dec final test.","benchmark":"+10 NIFTY before -5 within 15m; same 1m candle target+stop=LOSS","source":HF_REPO,"loaded_expiry_files":loaded,"tested_days":len(set(tested)),"skipped_days":skipped,"candidate_count":len(rules),"best_pre_final":best,"top_20_pre_final":rules[:20]}
    if best:
        final=apply_rule(rows[rows.month.isin([9,10,11,12])],best);n,w,l,rate=score(final)
        payload["sep_dec_final"]={"trades":n,"wins":w,"losses":l,"win_rate_pct":rate,"max_losing_streak":max_losing_streak(final),"median_mfe":round(float(final.mfe.median()),2) if n else 0.0,"median_mae":round(float(final.mae.median()),2) if n else 0.0,"signals":[{"entry_time":r.entry_time.isoformat(),"side":r.side,"result":r.result,"mfe":round(float(r.mfe),2),"mae":round(float(r.mae),2)} for r in final.itertuples()]}
        payload["accept_70_90"] = bool(n>=20 and 70.0<=rate<=90.0 and max_losing_streak(final)<=3)
    else:
        payload["sep_dec_final"]={"trades":0,"wins":0,"losses":0,"win_rate_pct":0.0};payload["accept_70_90"]=False
    Path(a.output).write_text(json.dumps(payload,indent=2),encoding="utf-8");print(json.dumps(payload,indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
