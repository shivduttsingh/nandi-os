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
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle

@dataclass(frozen=True)
class Event:
    day:date; signal_time:datetime; direction:str; strike:int
    spot_move_atr:float; premium_bias_pct:float; oi_bias_pct:float; volume_bias:float
    option_premium:float; option_high:float; series_key:tuple[date,str,int]

@dataclass(frozen=True)
class Config:
    lookback:int; min_spot_atr:float; min_premium_bias:float; min_oi_bias:float; min_volume_bias:float
    min_premium:float; target_points:float; stop_points:float; cooldown_minutes:int; max_trades_day:int=5

@dataclass(frozen=True)
class Trade:
    day:date; signal_time:datetime; entry_time:datetime; direction:str; strike:int; outcome:str; net_points:float


def atr(candles,lookback=14):
    if len(candles)<2:return 0.0
    sample=candles[-min(lookback,len(candles)-1):]; prev=candles[-len(sample)-1].close if len(candles)>len(sample) else candles[0].close; vals=[]
    for c in sample:
        vals.append(max(c.high-c.low,abs(c.high-prev),abs(c.low-prev)));prev=c.close
    return mean(vals) if vals else 0.0

def pct(old,new):return ((new/old)-1)*100 if old>0 else 0.0

def vol_ratio(hist):
    if len(hist)<8:return 0.0
    base=[c.volume for c in hist[-8:-1] if c.volume>0]
    if not base:return 0.0
    m=median(base);return hist[-1].volume/m if m>0 else 0.0

def metrics(hist,lookback):
    if len(hist)<lookback+1:return None
    old=hist[-lookback-1];cur=hist[-1]
    pm=pct(old.close,cur.close)
    oi=pct(old.open_interest,cur.open_interest) if old.open_interest>0 and cur.open_interest>0 else 0.0
    return pm,oi,vol_ratio(hist)


def build_events(spot_by_day,option_rows_by_day,lookback):
    events=[];series_map={};times_map={}
    for d in sorted(spot_by_day):
        spot=spot_by_day[d];raw=option_rows_by_day.get(d,[])
        if len(spot)<80 or not raw:continue
        at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            k=int(r.strike);c=_row_to_candle(r);at[r.timestamp].append((side,k,c));lists[(side,k)].append(c);strikes[side].add(k)
        common=sorted(strikes['CE']&strikes['PE'])
        if not common:continue
        for (side,k),vals in lists.items():
            sk=(d,side,k);series=tuple(sorted(vals,key=lambda x:x.timestamp));series_map[sk]=series;times_map[sk]=[x.timestamp for x in series]
        hist=defaultdict(list);n1=[]
        for c in spot:
            n1.append(c)
            for side,k,oc in at.get(c.timestamp,[]):hist[(side,k)].append(oc)
            if not(time(9,25)<=c.timestamp.time()<=time(14,30)) or len(n1)<20:continue
            a=atr(n1[-20:]);
            if a<=0:continue
            atm=_nearest_common_strike(strikes,c.close)
            if atm is None:continue
            near=sorted(common,key=lambda k:abs(k-c.close))[:5]
            ce=[];pe=[]
            for k in near:
                cm=metrics(hist.get(('CE',k),[]),lookback);pm=metrics(hist.get(('PE',k),[]),lookback)
                if cm is not None and pm is not None:ce.append(cm);pe.append(pm)
            if len(ce)<2:continue
            ce_move=mean(x[0] for x in ce);pe_move=mean(x[0] for x in pe)
            ce_oi=mean(x[1] for x in ce);pe_oi=mean(x[1] for x in pe)
            ce_vol=mean(x[2] for x in ce);pe_vol=mean(x[2] for x in pe)
            spot_move=(c.close-n1[-lookback-1].close)/a if len(n1)>=lookback+1 else 0.0
            direction='CE' if spot_move>0 else 'PE' if spot_move<0 else ''
            if not direction:continue
            chosen=hist.get((direction,atm),[])
            if not chosen:continue
            if direction=='CE':
                premium_bias=ce_move-pe_move;oi_bias=pe_oi-ce_oi;volume_bias=ce_vol-pe_vol
            else:
                spot_move=-spot_move;premium_bias=pe_move-ce_move;oi_bias=ce_oi-pe_oi;volume_bias=pe_vol-ce_vol
            events.append(Event(d,c.timestamp,direction,atm,spot_move,premium_bias,oi_bias,volume_bias,chosen[-1].close,chosen[-1].high,(d,direction,atm)))
    events.sort(key=lambda e:e.signal_time);return events,series_map,times_map


def qualifies(e,c):
    return e.spot_move_atr>=c.min_spot_atr and e.premium_bias_pct>=c.min_premium_bias and e.oi_bias_pct>=c.min_oi_bias and e.volume_bias>=c.min_volume_bias and e.option_premium>=c.min_premium


def simulate(e,c,series,times):
    if not series or not times:return None
    start=bisect_right(times,e.signal_time);trigger=e.option_high+0.10;deadline=e.signal_time+timedelta(minutes=2);idx=-1;entry=0.0
    for i in range(start,len(series)):
        b=series[i]
        if b.timestamp.date()!=e.day or b.timestamp>deadline:break
        if b.high>=trigger:idx=i;entry=max(trigger,b.open)+0.20;break
    if idx<0:return None
    stop=max(.05,entry-c.stop_points);target=entry+c.target_points;et=series[idx].timestamp;cut=et+timedelta(minutes=20);future=[b for b in series[idx:] if b.timestamp.date()==e.day and b.timestamp<=cut]
    if not future:return None
    for b in future:
        if b.low<=stop:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'LOSS',-c.stop_points-.50)
        if b.high>=target:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'WIN',c.target_points-.50)
    return Trade(e.day,e.signal_time,et,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-.50)


def stats(trades):
    t=tuple(trades);n=len(t);w=sum(x.outcome=='WIN' for x in t);l=sum(x.outcome=='LOSS' for x in t);to=n-w-l;net=sum(x.net_points for x in t);g=sum(max(0,x.net_points) for x in t);lv=abs(sum(min(0,x.net_points) for x in t));pf=g/lv if lv else (g if g else 0);eq=peak=dd=0
    for x in t:eq+=x.net_points;peak=max(peak,eq);dd=max(dd,peak-eq)
    return {'trades':n,'wins':w,'losses':l,'timeouts':to,'win_rate':round(100*w/n,2) if n else 0,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0,'profit_factor':round(pf,2),'max_drawdown':round(dd,2)}

def daily(trades,days,start,end):
    active=[d for d in days if start<=d<=end];cnt=defaultdict(int);net=defaultdict(float)
    for t in trades:cnt[t.day]+=1;net[t.day]+=t.net_points
    n=len(active);return {'trading_days':n,'avg_trades_per_day':round(len(trades)/n,2) if n else 0,'avg_net_points_per_day':round(sum(net.values())/n,2) if n else 0,'pct_days_with_3plus_trades':round(100*sum(cnt[d]>=3 for d in active)/n,2) if n else 0,'pct_profitable_days':round(100*sum(net[d]>0 for d in active)/n,2) if n else 0,'pct_days_15plus_points':round(100*sum(net[d]>=15 for d in active)/n,2) if n else 0}

def evaluate(events,c,sm,tm,days,start,end):
    by=defaultdict(list)
    for e in events:
        if start<=e.day<=end and qualifies(e,c):by[e.day].append(e)
    out=[]
    for d in [x for x in days if start<=x<=end]:
        last=None;seen=set()
        for e in by.get(d,[]):
            if sum(t.day==d for t in out)>=c.max_trades_day:break
            if last is not None and (e.signal_time-last).total_seconds()<c.cooldown_minutes*60:continue
            key=(e.signal_time,e.direction,e.strike)
            if key in seen:continue
            tr=simulate(e,c,sm.get(e.series_key),tm.get(e.series_key))
            if tr is None:continue
            out.append(tr);last=tr.entry_time;seen.add(key)
    return stats(out),daily(out,days,start,end),tuple(out)

def grid(lookback):
    for spot,prem,oi,vol,minp,pair,cd in itertools.product((0.05,0.15,0.30),(0.2,0.5,1.0),(0.0,1.0,3.0),(-0.2,0.0,0.2),(30.0,50.0),((8.0,4.0),(10.0,5.0)),(6,10)):
        yield Config(lookback,spot,prem,oi,vol,minp,pair[0],pair[1],cd)

def rank(st,ds):
    n=st['trades']
    if n<ds['trading_days']*2 or ds['avg_trades_per_day']<2:return -1e9
    if st['expectancy']<=0 or st['profit_factor']<=1:return -1e9
    p=st['wins']/n;z=1;lb=(p+z*z/(2*n)-z*math.sqrt((p*(1-p)+z*z/(4*n))/n))/(1+z*z/n)
    return lb*100+st['profit_factor']*4+st['expectancy']*2+ds['avg_net_points_per_day']+ds['pct_profitable_days']/10

def exact(p):
    st,ds=p['stats'],p['daily'];return ds['trading_days']>=55 and 3<=ds['avg_trades_per_day']<=5 and ds['pct_days_with_3plus_trades']>=65 and st['win_rate']>=70 and st['expectancy']>=3.5 and st['profit_factor']>=2 and ds['avg_net_points_per_day']>=15 and ds['pct_profitable_days']>=70

def payload_period(events,c,sm,tm,days,start,end):
    st,ds,tr=evaluate(events,c,sm,tm,days,start,end);return {'stats':st,'daily':ds,'trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in tr]}

def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));spot=_parse_spot_frame(path);opt=_parse_option_frame(path);lo,hi=date(2025,7,1),date(2026,6,30);spot=spot[(spot.timestamp.dt.date>=lo)&(spot.timestamp.dt.date<=hi)];opt=opt[(opt.day>=lo)&(opt.day<=hi)]
    sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot.groupby(spot.timestamp.dt.date,sort=True)};rows=defaultdict(list)
    for r in opt.itertuples(index=False):rows[r.day].append(r)
    days=sorted(sbd);train=(date(2025,7,1),date(2025,12,31));valw=(date(2026,1,1),date(2026,3,31));stressw=(date(2026,4,1),date(2026,6,30));ranked=[];near=[];counts={}
    for lb in (3,5):
        ev,sm,tm=build_events(sbd,rows,lb);counts[str(lb)]=len(ev)
        for c in grid(lb):
            st,ds,_=evaluate(ev,c,sm,tm,days,*train);raw=st['win_rate']+st['profit_factor']*3+st['expectancy']*2+ds['avg_trades_per_day']*4+ds['avg_net_points_per_day'];near.append((raw,c,st,ds,ev,sm,tm));sc=rank(st,ds)
            if sc>-1e8:ranked.append((sc,c,st,ds,ev,sm,tm))
    near.sort(key=lambda x:x[0],reverse=True);ranked.sort(key=lambda x:x[0],reverse=True);best=None
    if near:
        _,c,st,ds,_,_,_=near[0];best={'config':asdict(c),'stats':st,'daily':ds}
    if not ranked:out={'search_name':'Shiv Multi-Strike Chain Pressure Proof','status':'NO_TRAINING_CANDIDATE','event_counts':counts,'method':'Nearest common strikes around ATM; aggregate CE/PE premium return, OI change and volume pressure plus spot ATR momentum. Frozen Jul-Dec 2025 selection; real ATM option entry.','best_available_training':best}
    else:
        _,c,trst,trds,ev,sm,tm=ranked[0];val=payload_period(ev,c,sm,tm,days,*valw);stress=payload_period(ev,c,sm,tm,days,*stressw);comb=payload_period(ev,c,sm,tm,days,valw[0],stressw[1]);proven=exact(val) and exact(stress) and comb['stats']['win_rate']>=70 and comb['daily']['avg_net_points_per_day']>=15;out={'search_name':'Shiv Multi-Strike Chain Pressure Proof','status':'PROVEN_EXACT_TARGET' if proven else 'NO_EXACT_TARGET_PROVEN','event_counts':counts,'proof_rule':'Jan-Mar AND Apr-Jun each: 3-5 trades/day, >=70% wins, PF>=2, expectancy>=3.5, >=15 net option pts/day, >=70% profitable days.','chosen_config':asdict(c),'training':{'stats':trst,'daily':trds},'validation':val,'stress':stress,'combined_oos':comb,'best_available_training':best}
    Path('strategy_70_chain_pressure.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
