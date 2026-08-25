from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import product
from pathlib import Path

import pandas as pd

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.atm_strategy import assess_atm_confirmation
from nandi_v2.strike_window_strategy import assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as market
from scripts import search_nandi_high_precision_hf as base
from scripts.run_shiv_aplus_hf_backtest import load_hf_frames


def focused_build_rows(start: date, end: date, cache: Path):
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    rows, tested_days, skipped_days = [], set(), []
    for day in sorted(d for d in set(spot_df["day"]) if start <= d <= end):
        expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not expiries or (expiries[0] - day).days > 7:
            skipped_days.append(day.isoformat()); continue
        expiry = expiries[0]
        ds = spot_df[spot_df["day"] == day]
        dop = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if ds.empty or dop.empty:
            skipped_days.append(day.isoformat()); continue
        day_spot = [market.candle_from(r) for r in ds.itertuples(index=False)]
        full_now = datetime.combine(day, time(15,31))
        spot5 = market.aggregate(day_spot, 5, full_now)
        opt5, first = {}, {}
        for (strike, side), group in dop.groupby(["strike","side"], sort=False):
            raw = [market.candle_from(r) for r in group.sort_values("timestamp").itertuples(index=False)]
            if not raw: continue
            key=(int(strike),str(side)); first[key]=raw[0].timestamp
            opt5[key]=market.aggregate(raw,5,full_now)
        common=sorted(s for s in {k[0] for k in opt5} if (s,"CE") in opt5 and (s,"PE") in opt5)
        if len(common)<5:
            skipped_days.append(day.isoformat()); continue
        tested_days.add(day)
        now=datetime.combine(day,time(9,30)); end_ts=datetime.combine(day,time(14,45))
        while now<=end_ts:
            primary=base._completed(spot5,now)
            if len(primary)<5:
                now+=timedelta(minutes=5); continue
            visible=[s for s in common if first.get((s,"CE"),now+timedelta(days=1))<now and first.get((s,"PE"),now+timedelta(days=1))<now]
            if len(visible)<5:
                now+=timedelta(minutes=5); continue
            ai=min(range(len(visible)),key=lambda i:abs(visible[i]-primary[-1].close))
            if ai<2 or ai+2>=len(visible):
                now+=timedelta(minutes=5); continue
            strikes=visible[ai-2:ai+3]
            window=tuple(OptionStrikeCandles(float(s),expiry.isoformat(),off,base._completed(opt5[(s,"CE")],now),base._completed(opt5[(s,"PE")],now)) for off,s in enumerate(strikes,start=-2))
            if any(not x.ce_candles or not x.pe_candles for x in window):
                now+=timedelta(minutes=5); continue
            atm=assess_atm_confirmation(primary,window[2].ce_candles,window[2].pe_candles)
            win=assess_strike_window_confirmation(primary,window)
            atm_side=base._direction(atm.signal); win_side=base._direction(win.signal)
            # Focused family requires agreement, score floors and aligned structure.
            if atm_side not in {"CE","PE"} or atm_side!=win_side or atm.agreement_score<90 or win.agreement_score<80:
                now+=timedelta(minutes=5); continue
            side=atm_side
            aligned=win.nifty_structure == ("BULLISH" if side=="CE" else "BEARISH")
            if not aligned:
                now+=timedelta(minutes=5); continue
            base_row={
                "timestamp":now,"day":day,"month":day.month,"side":side,
                "atm_match":True,"atm_score":float(atm.agreement_score),
                "window_match":True,"window_score":float(win.agreement_score),
                "oi_supports":win.oi_confirmation=="SUPPORTS",
                "volume_expanding":win.volume_confirmation=="EXPANDING",
                "structure_aligned":True,"trend_eff":float(win.trend_efficiency or 0),
                "weighted_dom":float(win.weighted_dominance_pct),"persistence":int(win.persistence_bars),
                "minute":now.hour*60+now.minute,
            }
            for gate in ("immediate","body","breakout"):
                et=base._entry_time(day_spot,now,side,gate,primary[-1])
                out=base._outcome(day_spot,et,side)
                if out is not None:
                    rows.append({**base_row,"gate":gate,**out})
            now+=timedelta(minutes=5)
    return pd.DataFrame(rows), loaded, sorted(tested_days), skipped_days


def focused_select_rule(rows):
    march=rows[rows["month"]==3]; april=rows[rows["month"]==4]
    candidates=[]
    for side_mode,atm_min,win_min,oi_req,vol_req,eff_min,dom_min,gate in product(
        ("CE","PE","BOTH"),(90,95),(80,90),(False,True),(False,True),(0.0,0.4),(70,80),("immediate","body","breakout")
    ):
        def filt(df):
            m=(df.atm_score>=atm_min)&(df.window_score>=win_min)&(df.weighted_dom>=dom_min)&(df.gate==gate)
            if side_mode!="BOTH": m &= df.side==side_mode
            if oi_req: m &= df.oi_supports
            if vol_req: m &= df.volume_expanding
            if eff_min: m &= df.trend_eff>=eff_min
            return df[m]
        mn,mw,mr=base._score(filt(march)); an,aw,ar=base._score(filt(april))
        if mn<8 or an<8: continue
        candidates.append({"side":side_mode,"atm_min":atm_min,"window_min":win_min,"oi_required":oi_req,"volume_required":vol_req,"structure_required":True,"trend_eff_min":eff_min,"weighted_dom_min":dom_min,"gate":gate,"march_n":mn,"march_win_rate":mr,"april_n":an,"april_win_rate":ar,"robust_rate":min(mr,ar),"total_dev_trades":mn+an})
    candidates.sort(key=lambda x:(x["robust_rate"],min(x["march_n"],x["april_n"]),x["total_dev_trades"]),reverse=True)
    return candidates


base.build_rows=focused_build_rows
base.select_rule=focused_select_rule

if __name__=="__main__":
    raise SystemExit(base.main())
