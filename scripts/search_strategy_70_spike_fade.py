from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, time, timedelta
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle

@dataclass(frozen=True)
class Event:
    day:date;signal_time:object;direction:str;strike:int;spike_points:float;body_ratio:float;chosen_drop_pct:float;volume_ratio:float;oi_change_pct:float;premium:float;option_high:float;series_key:tuple[date,str,int]
@dataclass(frozen=True)
class Candidate:
    min_spike_points:float;min_body_ratio:float;min_chosen_drop_pct:float;min_volume_ratio:float;min_oi_change_pct:float;min_premium:float;start_hour:int;start_minute:int;end_hour:int;end_minute:int;target_points:float;stop_points:float;max_hold_minutes:int
@dataclass(frozen=True)
class Trade:
    day:date;signal_time:object;direction:str;strike:int;outcome:str;net_points:float

def pct(a,b):return ((b/a)-1)*100 if a>0 else 0.0
def move(c,lb=3):return pct(c[-lb-1].close,c[-1].close) if len(c)>=lb+1 else 0.0
def oi(c,lb=3):
    if len(c)<lb+1:return 0.0
    old=c[-lb-1].open_interest;return pct(old,c[-1].open_interest) if old>0 else 0.0
def vr(c):
    hist=[x.volume for x in c[-10:-1] if x.volume>0]
    if not hist:return 0.0
    b=median(hist);return c[-1].volume/b if b>0 else 0.0

def build_events(spot_by_day,rows):
    events=[];smap={};tmap={}
    for d in sorted(spot_by_day):
        day=spot_by_day[d];raw=rows.get(d,[])
        if len(day)<100 or not raw:continue
        at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            st=int(r.strike);cc=_row_to_candle(r);at[r.timestamp].append((side,r));lists[(side,st)].append(cc);strikes[side].add(st)
        if not(strikes['CE']&strikes['PE']):continue
        for k,v in lists.items():
            sk=(d,k[0],k[1]);ss=tuple(sorted(v,key=lambda x:x.timestamp));smap[sk]=ss;tmap[sk]=[x.timestamp for x in ss]
        hist=defaultdict(list)
        for spot in day:
            for side,r in at.get(spot.timestamp,[]):hist[(side,int(r.strike))].append(_row_to_candle(r))
            if not(time(9,30)<=spot.timestamp.time()<=time(13,30)):continue
            bar_move=spot.close-spot.open
            if abs(bar_move)<20:continue
            direction='PE' if bar_move>0 else 'CE'
            st=_nearest_common_strike(strikes,spot.close)
            if st is None:continue
            chosen=hist.get((direction,st),[])
            if len(chosen)<10:continue
            rng=max(spot.high-spot.low,1e-9);body=abs(bar_move)/rng;drop=max(0.0,-move(chosen,3))
            events.append(Event(d,spot.timestamp,direction,st,abs(bar_move),body,drop,vr(chosen),oi(chosen,3),chosen[-1].close,chosen[-1].high,(d,direction,st)))
    events.sort(key=lambda e:e.signal_time);return events,smap,tmap

def qualifies(e,c):
    t=e.signal_time.time();return e.spike_points>=c.min_spike_points and e.body_ratio>=c.min_body_ratio and e.chosen_drop_pct>=c.min_chosen_drop_pct and e.volume_ratio>=c.min_volume_ratio and e.oi_change_pct>=c.min_oi_change_pct and e.premium>=c.min_premium and time(c.start_hour,c.start_minute)<=t<=time(c.end_hour,c.end_minute)
def simulate(e,c,s,ts):
    start=bisect_right(ts,e.signal_time);trigger=e.option_high+.10;deadline=e.signal_time+timedelta(minutes=2);idx=-1;entry=0.0
    for i in range(start,len(s)):
        x=s[i]
        if x.timestamp.date()!=e.day or x.timestamp>deadline:break
        if x.high>=trigger:idx=i;entry=max(trigger,x.open)+.20;break
    if idx<0:return None
    stop=max(.05,entry-c.stop_points);target=entry+c.target_points;cut=s[idx].timestamp+timedelta(minutes=c.max_hold_minutes);future=[x for x in s[idx:] if x.timestamp.date()==e.day and x.timestamp<=cut]
    if not future:return None
    for x in future:
        if x.low<=stop:return Trade(e.day,e.signal_time,e.direction,e.strike,'LOSS',-c.stop_points-.50)
        if x.high>=target:return Trade(e.day,e.signal_time,e.direction,e.strike,'WIN',c.target_points-.50)
    return Trade(e.day,e.signal_time,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-.50)
def stats(trades):
    trades=tuple(trades);n=len(trades);w=sum(x.outcome=='WIN' for x in trades);l=sum(x.outcome=='LOSS' for x in trades);to=sum(x.outcome=='TIMEOUT' for x in trades);net=sum(x.net_points for x in trades);g=sum(max(0,x.net_points) for x in trades);lv=abs(sum(min(0,x.net_points) for x in trades));pf=g/lv if lv else(g if g else 0);eq=pk=dd=0
    for x in trades:eq+=x.net_points;pk=max(pk,eq);dd=max(dd,pk-eq)
    return {'trades':n,'wins':w,'losses':l,'timeouts':to,'win_rate':round(100*w/n,2) if n else 0.0,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.0,'profit_factor':round(pf,2),'max_drawdown':round(dd,2)}
def evaluate(events,c,smap,tmap,start,end):
    by=defaultdict(list)
    for e in events:
        if start<=e.day<=end and qualifies(e,c):by[e.day].append(e)
    out=[]
    for d in sorted(by):
        for e in by[d]:
            s=smap.get(e.series_key);ts=tmap.get(e.series_key)
            if not s or not ts:continue
            tr=simulate(e,c,s,ts)
            if tr is not None:out.append(tr);break
    return stats(out),tuple(out)
def grid(selective=False):
    exits=((6.,4.,25),(8.,5.,30),(10.,6.,35)) if selective else ((3.,4.,20),(4.,5.,25),(4.,4.,25))
    out=[]
    for v in itertools.product((25.,35.,45.,55.),(.50,.70),(.5,1.5,3.0),(.8,1.2),(-10.,0.),(30.,50.),((9,30),(10,0)),((11,30),(13,30)),exits):
        out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6][0],v[6][1],v[7][0],v[7][1],v[8][0],v[8][1],v[8][2]))
    return out
def rank(s,selective):
    minimum=10 if selective else 18
    if s['trades']<minimum or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
    p=s['wins']/s['trades'];z=1;lb=(p+z*z/(2*s['trades'])-z*math.sqrt((p*(1-p)+z*z/(4*s['trades']))/s['trades']))/(1+z*z/s['trades']);return lb*100+min(s['profit_factor'],4)*5+min(s['expectancy'],5)*(4 if selective else 2)-s['max_drawdown']/60
def mend(y,m):return date(y,12,31) if m==12 else date(y,m+1,1)-timedelta(days=1)
def walk(name,candidates,events,smap,tmap,lo,selective):
    folds=[];oos=[]
    for y,m in ((2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)):
        a=date(y,m,1);b=mend(y,m);ranked=[]
        for c in candidates:
            tr,_=evaluate(events,c,smap,tmap,lo,a-timedelta(days=1));sc=rank(tr,selective)
            if sc>-1e8:ranked.append((sc,c,tr))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:folds.append({'month':a.strftime('%Y-%m'),'status':'NO_TRAINING_CANDIDATE'});continue
        _,c,tr=ranked[0];te,tt=evaluate(events,c,smap,tmap,a,b);oos.extend(tt);folds.append({'month':a.strftime('%Y-%m'),'candidate_frozen_before_month':True,'candidate':asdict(c),'training':tr,'test':te,'trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat()} for x in tt]})
    ag=stats(oos);pos=sum(1 for f in folds if 'test'in f and f['test']['expectancy']>0);sixty=sum(1 for f in folds if 'test'in f and f['test']['win_rate']>=60);active=sum(1 for f in folds if 'test'in f and f['test']['trades']>0);avg=ag['trades']/active if active else 0
    if selective:passed=12<=ag['trades']<=30 and ag['win_rate']>=70 and ag['expectancy']>=1.5 and ag['profit_factor']>=1.5 and pos>=4 and sixty>=4 and avg<=4
    else:passed=ag['trades']>=20 and ag['win_rate']>=70 and ag['expectancy']>0 and ag['profit_factor']>=1.2 and pos>=4 and sixty>=4
    return {'name':name,'status':'PROVEN_70_PLUS' if passed else 'NO_70_PLUS_CANDIDATE_PROVEN','aggregate_oos':ag,'positive_months':pos,'sixty_plus_months':sixty,'active_months':active,'avg_trades_per_active_month':round(avg,2),'folds':folds}
def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(path);op=_parse_option_frame(path);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
    for r in op.itertuples(index=False):rows[r.day].append(r)
    events,smap,tmap=build_events(sbd,rows);accuracy=walk('Spike Fade Accuracy',grid(False),events,smap,tmap,lo,False);selective=walk('Spike Fade Selective Profit',grid(True),events,smap,tmap,lo,True);payload={'search_name':'Shiv 1m Spike-Fade Monthly Walk-Forward','events_built':len(events),'accuracy':accuracy,'selective_profit':selective,'overall_status':'BOTH_PROVEN' if accuracy['status']=='PROVEN_70_PLUS' and selective['status']=='PROVEN_70_PLUS' else 'NOT_BOTH_PROVEN'};Path('strategy_70_spike_fade.json').write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
