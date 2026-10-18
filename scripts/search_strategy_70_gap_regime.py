from __future__ import annotations

import itertools, json, math, sys
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
    day:date; signal_time:datetime; mode:str; direction:str; strike:int
    gap_abs_pct:float; break_extension_atr:float; trend_gap_atr:float
    option_outperformance:float; option_volume_ratio:float; option_ema_aligned:bool
    option_premium:float; option_high:float; series_key:tuple[date,str,int]
@dataclass(frozen=True)
class Candidate:
    mode:str; min_gap_pct:float; max_extension_atr:float; min_trend_gap_atr:float
    min_outperformance:float; min_volume_ratio:float; require_option_ema:bool; min_premium:float
    target_points:float; stop_points:float
@dataclass(frozen=True)
class Trade:
    day:date; signal_time:datetime; entry_time:datetime; direction:str; strike:int; outcome:str; net_points:float
@dataclass(frozen=True)
class Stats:
    trades:int; wins:int; losses:int; timeouts:int; win_rate:float; net_points:float; expectancy:float; profit_factor:float; max_drawdown:float

def pct(a,b): return ((b/a)-1)*100 if a>0 else 0.0
def ema(v,p):
    if not v:return 0.0
    a=2/(p+1); o=v[0]
    for x in v[1:]:o=a*x+(1-a)*o
    return o
def atr(c,lookback=14):
    if len(c)<2:return 0.0
    s=c[-min(lookback,len(c)-1):]; prev=c[-len(s)-1].close if len(c)>len(s) else c[0].close; vals=[]
    for x in s: vals.append(max(x.high-x.low,abs(x.high-prev),abs(x.low-prev))); prev=x.close
    return mean(vals) if vals else 0.0
def vr(c,lookback=12):
    h=[x.volume for x in c[-lookback-1:-1] if x.volume>0]
    if not h:return 0.0
    b=median(h);return c[-1].volume/b if b>0 else 0.0
def mv(c,lb=3):
    return pct(c[-lb-1].close,c[-1].close) if len(c)>=lb+1 else 0.0

def build_events(sbd,rows):
    events=[]; smap={}; tmap={}; days=sorted(sbd); prevmap={days[i]:days[i-1] for i in range(1,len(days))}
    for d in days[1:]:
        day=sbd[d]; prev=sbd[prevmap[d]]; raw=rows.get(d,[])
        if len(day)<100 or len(prev)<100 or not raw:continue
        prev_close=prev[-1].close; day_open=day[0].open; gap=pct(prev_close,day_open)
        if abs(gap)<0.08:continue
        start=datetime.combine(d,time(9,15)); end=start+timedelta(minutes=15); opening=[x for x in day if start<=x.timestamp<end]
        if len(opening)<12:continue
        orh=max(x.high for x in opening); orl=min(x.low for x in opening)
        at=defaultdict(list); lists=defaultdict(list); strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            st=int(r.strike); cc=_row_to_candle(r);at[r.timestamp].append((side,r));lists[(side,st)].append(cc);strikes[side].add(st)
        if not(strikes['CE']&strikes['PE']):continue
        for k,v in lists.items():
            sk=(d,k[0],k[1]);ss=tuple(sorted(v,key=lambda x:x.timestamp));smap[sk]=ss;tmap[sk]=[x.timestamp for x in ss]
        n1=[];n5=[];hist=defaultdict(list)
        for spot in day:
            n1.append(spot);_update_aggregate(n5,spot,5)
            for side,r in at.get(spot.timestamp,[]):hist[(side,int(r.strike))].append(_row_to_candle(r))
            if not(time(9,35)<=spot.timestamp.time()<=time(11,15)) or len(n1)<25 or len(n5)<5:continue
            a=atr(n1[-20:])
            if a<=0:continue
            c=n1[-1];p=n1[-2];cl=[x.close for x in n5[-10:]];fast=ema(cl,5);slow=ema(cl,9);tg=abs(fast-slow)/a
            candidates=[]
            if gap>0:
                if c.close>orh and c.close>c.open and c.close>p.close and n5[-1].close>fast>slow:candidates.append(('CONT','CE',max(0,c.close-orh)/a))
                if c.close<orl and c.close<c.open and c.close<p.close and n5[-1].close<fast<slow:candidates.append(('FADE','PE',max(0,orl-c.close)/a))
            else:
                if c.close<orl and c.close<c.open and c.close<p.close and n5[-1].close<fast<slow:candidates.append(('CONT','PE',max(0,orl-c.close)/a))
                if c.close>orh and c.close>c.open and c.close>p.close and n5[-1].close>fast>slow:candidates.append(('FADE','CE',max(0,c.close-orh)/a))
            for mode,direction,ext in candidates:
                st=_nearest_common_strike(strikes,spot.close)
                if st is None:continue
                chosen=hist.get((direction,st),[]);opp=hist.get(('PE' if direction=='CE' else 'CE',st),[])
                if len(chosen)<15 or len(opp)<5:continue
                cm=mv(chosen);om=mv(opp);oc=[x.close for x in chosen[-20:]];of=ema(oc,5);os=ema(oc,13)
                events.append(Event(d,spot.timestamp,mode,direction,st,abs(gap),ext,tg,cm-om,vr(chosen),chosen[-1].close>of>os,chosen[-1].close,chosen[-1].high,(d,direction,st)))
    events.sort(key=lambda e:e.signal_time);return events,smap,tmap

def q(e,c):return e.mode==c.mode and e.gap_abs_pct>=c.min_gap_pct and e.break_extension_atr<=c.max_extension_atr and e.trend_gap_atr>=c.min_trend_gap_atr and e.option_outperformance>=c.min_outperformance and e.option_volume_ratio>=c.min_volume_ratio and (not c.require_option_ema or e.option_ema_aligned) and e.option_premium>=c.min_premium
def sim(e,c,s,ts):
    start=bisect_right(ts,e.signal_time);tr=e.option_high+.10;dl=e.signal_time+timedelta(minutes=2);idx=-1;entry=0
    for i in range(start,len(s)):
        x=s[i]
        if x.timestamp.date()!=e.day or x.timestamp>dl:break
        if x.high>=tr:idx=i;entry=max(tr,x.open)+.20;break
    if idx<0:return None
    stop=max(.05,entry-c.stop_points);target=entry+c.target_points;et=s[idx].timestamp;cut=et+timedelta(minutes=25);f=[x for x in s[idx:] if x.timestamp.date()==e.day and x.timestamp<=cut]
    if not f:return None
    for x in f:
        if x.low<=stop:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'LOSS',-c.stop_points-.5)
        if x.high>=target:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'WIN',c.target_points-.5)
    return Trade(e.day,e.signal_time,et,e.direction,e.strike,'TIMEOUT',f[-1].close-entry-.5)
def stats(t):
    t=tuple(t);n=len(t);w=sum(x.outcome=='WIN' for x in t);l=sum(x.outcome=='LOSS' for x in t);to=sum(x.outcome=='TIMEOUT' for x in t);net=sum(x.net_points for x in t);g=sum(max(0,x.net_points) for x in t);lv=abs(sum(min(0,x.net_points) for x in t));pf=g/lv if lv else(g if g else 0);eq=pk=dd=0
    for x in t:eq+=x.net_points;pk=max(pk,eq);dd=max(dd,pk-eq)
    return Stats(n,w,l,to,round(100*w/n,2) if n else 0,round(net,2),round(net/n,2) if n else 0,round(pf,2),round(dd,2))
def ev(events,c,smap,tmap,start,end):
    by=defaultdict(list)
    for e in events:
        if start<=e.day<=end and q(e,c):by[e.day].append(e)
    out=[]
    for d in sorted(by):
        for e in by[d]:
            s=smap.get(e.series_key);ts=tmap.get(e.series_key)
            if not s or not ts:continue
            z=sim(e,c,s,ts)
            if z is not None:out.append(z);break
    return stats(out),tuple(out)
def grid():
    out=[];ex=[(4.,5.),(5.,6.),(5.,5.),(6.,6.)]
    for v in itertools.product(('CONT','FADE'),(.15,.30,.50),(.60,1.),(.03,.08),(.3,.8),(.9,1.2),(False,True),(30.,50.),ex):out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8][0],v[8][1]))
    return out
def score(s):
    if s.trades<18 or s.expectancy<=0 or s.profit_factor<=1:return -1e9
    p=s.wins/s.trades;z=1;lb=(p+z*z/(2*s.trades)-z*math.sqrt((p*(1-p)+z*z/(4*s.trades))/s.trades))/(1+z*z/s.trades);return lb*100+min(s.profit_factor,3)*5+min(s.expectancy,4)*2-s.max_drawdown/60
def mend(y,m):return date(y,12,31) if m==12 else date(y,m+1,1)-timedelta(days=1)
def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(path);op=_parse_option_frame(path);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
    for r in op.itertuples(index=False):rows[r.day].append(r)
    events,smap,tmap=build_events(sbd,rows);cs=grid();folds=[];oos=[]
    for y,m in [(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]:
        a=date(y,m,1);b=mend(y,m);rank=[]
        for c in cs:
            st,_=ev(events,c,smap,tmap,lo,a-timedelta(days=1));sc=score(st)
            if sc>-1e8:rank.append((sc,c,st))
        rank.sort(key=lambda x:x[0],reverse=True)
        if not rank:folds.append({'month':a.strftime('%Y-%m'),'status':'NO_TRAINING_CANDIDATE'});continue
        _,c,tr=rank[0];te,tt=ev(events,c,smap,tmap,a,b);oos.extend(tt);folds.append({'month':a.strftime('%Y-%m'),'candidate_frozen_before_month':True,'candidate':asdict(c),'training':asdict(tr),'test':asdict(te),'trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat(),'entry_time':x.entry_time.isoformat()} for x in tt]})
    ag=stats(oos);pos=sum(1 for f in folds if 'test'in f and f['test']['expectancy']>0);sixty=sum(1 for f in folds if 'test'in f and f['test']['win_rate']>=60);passed=ag.trades>=30 and ag.win_rate>=70 and ag.expectancy>0 and ag.profit_factor>1.2 and pos>=5 and sixty>=5;payload={'search_name':'Shiv Opening-Gap Regime Monthly Walk-Forward','candidate_count':len(cs),'events_built':len(events),'status':'PROVEN_70_PLUS' if passed else 'NO_70_PLUS_CANDIDATE_PROVEN','proof_rule':'>=30 aggregate OOS trades, >=70% wins, positive expectancy, PF>1.2, >=5 positive months and >=5 months >=60%.','aggregate_oos':asdict(ag),'positive_months':pos,'sixty_plus_months':sixty,'folds':folds};Path('strategy_70_gap_regime.json').write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
