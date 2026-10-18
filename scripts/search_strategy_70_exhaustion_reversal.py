from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle,_update_aggregate

@dataclass(frozen=True)
class Event:
    day:date; signal_time:datetime; direction:str; strike:int
    impulse_atr:float; wick_ratio:float; body_ratio:float; five_extension_atr:float
    option_move_1m_pct:float; option_outperformance_1m_pct:float; option_volume_ratio:float
    option_premium:float; option_high:float; series_key:tuple[date,str,int]

@dataclass(frozen=True)
class Candidate:
    min_impulse_atr:float; min_wick_ratio:float; min_body_ratio:float; min_five_extension_atr:float
    min_option_move_pct:float; min_outperformance_pct:float; min_volume_ratio:float; min_premium:float
    target_points:float; stop_points:float

@dataclass(frozen=True)
class Trade:
    day:date; signal_time:datetime; entry_time:datetime; direction:str; strike:int; outcome:str; net_points:float

@dataclass(frozen=True)
class Stats:
    trades:int; wins:int; losses:int; timeouts:int; win_rate:float; net_points:float; expectancy:float; profit_factor:float; max_drawdown:float

def pct(a,b): return ((b/a)-1)*100 if a>0 else 0.0

def ema(values,period):
    if not values:return 0.0
    alpha=2/(period+1); out=values[0]
    for x in values[1:]: out=alpha*x+(1-alpha)*out
    return out

def atr(candles,lookback=14):
    if len(candles)<2:return 0.0
    sample=candles[-min(lookback,len(candles)-1):]
    prev=candles[-len(sample)-1].close if len(candles)>len(sample) else candles[0].close
    vals=[]
    for c in sample:
        vals.append(max(c.high-c.low,abs(c.high-prev),abs(c.low-prev))); prev=c.close
    return mean(vals) if vals else 0.0

def vol_ratio(candles,lookback=12):
    hist=[c.volume for c in candles[-lookback-1:-1] if c.volume>0]
    if not hist:return 0.0
    b=median(hist); return candles[-1].volume/b if b>0 else 0.0

def build_events(spot_by_day,option_rows):
    events=[]; series_map={}; times_map={}
    for d in sorted(spot_by_day):
        day=spot_by_day[d]; raw=option_rows.get(d,[])
        if len(day)<100 or not raw:continue
        at_time=defaultdict(list); lists=defaultdict(list); strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            strike=int(r.strike); c=_row_to_candle(r)
            at_time[r.timestamp].append((side,r)); lists[(side,strike)].append(c); strikes[side].add(strike)
        if not(strikes['CE']&strikes['PE']):continue
        for k,v in lists.items():
            sk=(d,k[0],k[1]); s=tuple(sorted(v,key=lambda c:c.timestamp)); series_map[sk]=s; times_map[sk]=[c.timestamp for c in s]
        n1=[]; n5=[]; hist=defaultdict(list)
        for spot in day:
            n1.append(spot); _update_aggregate(n5,spot,5)
            for side,r in at_time.get(spot.timestamp,[]): hist[(side,int(r.strike))].append(_row_to_candle(r))
            if not(time(9,35)<=spot.timestamp.time()<=time(14,0)) or len(n1)<25 or len(n5)<5:continue
            a=atr(n1[-20:])
            if a<=0:continue
            pre_start=n1[-5].close; pre_end=n1[-2].close; impulse=(pre_end-pre_start)/a
            cur=n1[-1]; prev=n1[-2]
            rng=max(cur.high-cur.low,1e-9); body=abs(cur.close-cur.open)/rng
            direction=''; wick=0.0
            if impulse>0 and cur.close<cur.open and cur.close<((prev.open+prev.close)/2) and cur.close<prev.low:
                direction='PE'; wick=(cur.high-max(cur.open,cur.close))/rng
            elif impulse<0 and cur.close>cur.open and cur.close>((prev.open+prev.close)/2) and cur.close>prev.high:
                direction='CE'; impulse=-impulse; wick=(min(cur.open,cur.close)-cur.low)/rng
            else:continue
            closes5=[c.close for c in n5[-12:]]; e=ema(closes5,9); extension=abs(n5[-1].close-e)/a
            strike=_nearest_common_strike(strikes,spot.close)
            if strike is None:continue
            chosen=hist.get((direction,strike),[]); opposite_side='PE' if direction=='CE' else 'CE'; opp=hist.get((opposite_side,strike),[])
            if len(chosen)<12 or len(opp)<2:continue
            cm=pct(chosen[-2].close,chosen[-1].close); om=pct(opp[-2].close,opp[-1].close)
            events.append(Event(d,spot.timestamp,direction,strike,impulse,wick,body,extension,cm,cm-om,vol_ratio(chosen),chosen[-1].close,chosen[-1].high,(d,direction,strike)))
    events.sort(key=lambda e:e.signal_time); return events,series_map,times_map

def qualifies(e,c):
    return e.impulse_atr>=c.min_impulse_atr and e.wick_ratio>=c.min_wick_ratio and e.body_ratio>=c.min_body_ratio and e.five_extension_atr>=c.min_five_extension_atr and e.option_move_1m_pct>=c.min_option_move_pct and e.option_outperformance_1m_pct>=c.min_outperformance_pct and e.option_volume_ratio>=c.min_volume_ratio and e.option_premium>=c.min_premium

def simulate(e,c,series,times):
    start=bisect_right(times,e.signal_time); trigger=e.option_high+0.10; deadline=e.signal_time+timedelta(minutes=2); idx=-1; entry=0.0
    for i in range(start,len(series)):
        x=series[i]
        if x.timestamp.date()!=e.day or x.timestamp>deadline:break
        if x.high>=trigger: idx=i; entry=max(trigger,x.open)+0.20; break
    if idx<0:return None
    stop=max(0.05,entry-c.stop_points); target=entry+c.target_points; et=series[idx].timestamp; cutoff=et+timedelta(minutes=20)
    future=[x for x in series[idx:] if x.timestamp.date()==e.day and x.timestamp<=cutoff]
    if not future:return None
    for x in future:
        if x.low<=stop:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'LOSS',-c.stop_points-0.50)
        if x.high>=target:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'WIN',c.target_points-0.50)
    return Trade(e.day,e.signal_time,et,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-0.50)

def stats_for(trades):
    t=tuple(trades); n=len(t); w=sum(x.outcome=='WIN' for x in t); l=sum(x.outcome=='LOSS' for x in t); to=sum(x.outcome=='TIMEOUT' for x in t); net=sum(x.net_points for x in t); gain=sum(max(0,x.net_points) for x in t); loss=abs(sum(min(0,x.net_points) for x in t)); pf=gain/loss if loss else (gain if gain else 0); eq=peak=dd=0.0
    for x in t:eq+=x.net_points; peak=max(peak,eq); dd=max(dd,peak-eq)
    return Stats(n,w,l,to,round(100*w/n,2) if n else 0.0,round(net,2),round(net/n,2) if n else 0.0,round(pf,2),round(dd,2))
def evaluate(events,c,series_map,times_map,start,end):
    by=defaultdict(list)
    for e in events:
        if start<=e.day<=end and qualifies(e,c):by[e.day].append(e)
    trades=[]
    for d in sorted(by):
        for e in by[d]:
            s=series_map.get(e.series_key); ts=times_map.get(e.series_key)
            if not s or not ts:continue
            tr=simulate(e,c,s,ts)
            if tr is not None: trades.append(tr); break
    return stats_for(trades),tuple(trades)
def grid():
    out=[]; exits=[(3.0,5.0),(4.0,6.0),(5.0,6.0),(5.0,5.0)]
    for vals in itertools.product((0.8,1.2),(0.20,0.35),(0.35,0.55),(0.4,0.7),(0.1,0.5),(0.3,0.8),(0.9,1.3),(30.0,50.0),exits):
        out.append(Candidate(*vals[:-1],vals[-1][0],vals[-1][1]))
    return out
def rank_score(s):
    if s.trades<25 or s.expectancy<=0 or s.profit_factor<=1:return -1e9
    p=s.wins/s.trades; z=1.0; lb=(p+z*z/(2*s.trades)-z*math.sqrt((p*(1-p)+z*z/(4*s.trades))/s.trades))/(1+z*z/s.trades)
    return lb*100+min(s.profit_factor,3)*5+min(s.expectancy,4)*2-s.max_drawdown/60
def mend(y,m):return date(y,12,31) if m==12 else date(y,m+1,1)-timedelta(days=1)
def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx')); spot=_parse_spot_frame(path); opt=_parse_option_frame(path); lo,hi=date(2025,7,1),date(2026,6,30); spot=spot[(spot.timestamp.dt.date>=lo)&(spot.timestamp.dt.date<=hi)]; opt=opt[(opt.day>=lo)&(opt.day<=hi)]
    sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot.groupby(spot.timestamp.dt.date,sort=True)}; rows=defaultdict(list)
    for r in opt.itertuples(index=False):rows[r.day].append(r)
    events,series,times=build_events(sbd,rows); candidates=grid(); folds=[]; all_oos=[]
    for y,m in [(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]:
        ts=date(y,m,1); te=mend(y,m); train_end=ts-timedelta(days=1); ranked=[]
        for c in candidates:
            st,_=evaluate(events,c,series,times,lo,train_end); sc=rank_score(st)
            if sc>-1e8:ranked.append((sc,c,st))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:folds.append({'month':ts.strftime('%Y-%m'),'status':'NO_TRAINING_CANDIDATE'});continue
        _,c,trst=ranked[0]; tst,ttr=evaluate(events,c,series,times,ts,te);all_oos.extend(ttr);folds.append({'month':ts.strftime('%Y-%m'),'candidate_frozen_before_month':True,'candidate':asdict(c),'training':asdict(trst),'test':asdict(tst),'trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat(),'entry_time':x.entry_time.isoformat()} for x in ttr]})
    agg=stats_for(all_oos); pos=sum(1 for f in folds if 'test'in f and f['test']['expectancy']>0); sixty=sum(1 for f in folds if 'test'in f and f['test']['win_rate']>=60); passed=agg.trades>=35 and agg.win_rate>=70 and agg.expectancy>0 and agg.profit_factor>1.2 and pos>=5 and sixty>=5
    payload={'search_name':'Shiv Exhaustion-Reversal Monthly Walk-Forward','candidate_count':len(candidates),'events_built':len(events),'status':'PROVEN_70_PLUS' if passed else 'NO_70_PLUS_CANDIDATE_PROVEN','proof_rule':'>=35 aggregate OOS trades, >=70% wins, positive expectancy, PF>1.2, >=5 positive months and >=5 months >=60% wins.','aggregate_oos':asdict(agg),'positive_months':pos,'sixty_plus_months':sixty,'folds':folds};Path('strategy_70_exhaustion_reversal.json').write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
