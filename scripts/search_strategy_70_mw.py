from __future__ import annotations

import itertools,json,math,sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict,dataclass
from datetime import date,time,timedelta
from pathlib import Path
from statistics import mean,median

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle

@dataclass(frozen=True)
class Event:
    day:date;signal_time:object;direction:str;strike:int;lookback:int;similarity_atr:float;depth_atr:float;separation:int;option_move_pct:float;outperformance_pct:float;volume_ratio:float;oi_change_pct:float;ema_aligned:bool;premium:float;option_high:float;series_key:tuple[date,str,int]
@dataclass(frozen=True)
class Candidate:
    lookback:int;max_similarity_atr:float;min_depth_atr:float;min_separation:int;min_option_move_pct:float;min_outperformance_pct:float;min_volume_ratio:float;min_oi_change_pct:float;require_ema:bool;min_premium:float;end_hour:int;end_minute:int;target_points:float;stop_points:float;max_hold_minutes:int
@dataclass(frozen=True)
class Trade:
    day:date;signal_time:object;direction:str;strike:int;outcome:str;net_points:float

def pct(a,b):return ((b/a)-1)*100 if a>0 else 0.0
def ema(v,p):
    if not v:return 0.0
    a=2/(p+1);o=v[0]
    for x in v[1:]:o=a*x+(1-a)*o
    return o
def atr(c,lb=14):
    if len(c)<2:return 0.0
    s=c[-min(lb,len(c)-1):];pr=c[-len(s)-1].close if len(c)>len(s) else c[0].close;vals=[]
    for x in s:vals.append(max(x.high-x.low,abs(x.high-pr),abs(x.low-pr)));pr=x.close
    return mean(vals) if vals else 0.0
def move(c,lb=3):return pct(c[-lb-1].close,c[-1].close) if len(c)>=lb+1 else 0.0
def vr(c):
    h=[x.volume for x in c[-13:-1] if x.volume>0]
    if not h:return 0.0
    b=median(h);return c[-1].volume/b if b>0 else 0.0
def oi(c,lb=3):
    if len(c)<lb+1:return 0.0
    old=c[-lb-1].open_interest;return pct(old,c[-1].open_interest) if old>0 else 0.0

def patterns(n1,spot_atr,lookback):
    if len(n1)<lookback:return []
    sample=n1[-lookback:];hist=sample[:-1];cur=sample[-1];prev=sample[-2];half=len(hist)//2;first=hist[:half];second=hist[half:]
    out=[]
    # W: two comparable lows and break above neckline now.
    i1=min(range(len(first)),key=lambda i:first[i].low);i2rel=min(range(len(second)),key=lambda i:second[i].low);i2=half+i2rel
    low1=hist[i1].low;low2=hist[i2].low;between=hist[i1:i2+1]
    if between:
        neck=max(x.high for x in between);sim=abs(low1-low2)/spot_atr;depth=(neck-max(low1,low2))/spot_atr;sep=i2-i1
        if cur.close>neck and prev.close<=neck and cur.close>cur.open:out.append(('CE',sim,depth,sep))
    # M: two comparable highs and break below neckline now.
    j1=max(range(len(first)),key=lambda i:first[i].high);j2rel=max(range(len(second)),key=lambda i:second[i].high);j2=half+j2rel
    high1=hist[j1].high;high2=hist[j2].high;between2=hist[j1:j2+1]
    if between2:
        neck2=min(x.low for x in between2);sim2=abs(high1-high2)/spot_atr;depth2=(min(high1,high2)-neck2)/spot_atr;sep2=j2-j1
        if cur.close<neck2 and prev.close>=neck2 and cur.close<cur.open:out.append(('PE',sim2,depth2,sep2))
    return out

def build_events(sbd,rows):
    events=[];smap={};tmap={}
    for d in sorted(sbd):
        day=sbd[d];raw=rows.get(d,[])
        if len(day)<100 or not raw:continue
        at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            st=int(r.strike);cc=_row_to_candle(r);at[r.timestamp].append((side,r));lists[(side,st)].append(cc);strikes[side].add(st)
        if not(strikes['CE']&strikes['PE']):continue
        for k,v in lists.items():
            sk=(d,k[0],k[1]);ss=tuple(sorted(v,key=lambda x:x.timestamp));smap[sk]=ss;tmap[sk]=[x.timestamp for x in ss]
        hist=defaultdict(list);n1=[]
        for spot in day:
            n1.append(spot)
            for side,r in at.get(spot.timestamp,[]):hist[(side,int(r.strike))].append(_row_to_candle(r))
            if not(time(9,40)<=spot.timestamp.time()<=time(13,0)) or len(n1)<32:continue
            a=atr(n1[-20:])
            if a<=0:continue
            st=_nearest_common_strike(strikes,spot.close)
            if st is None:continue
            for lookback in (20,30):
                for direction,sim,depth,sep in patterns(n1,a,lookback):
                    chosen=hist.get((direction,st),[]);opp=hist.get(('PE' if direction=='CE' else 'CE',st),[])
                    if len(chosen)<15 or len(opp)<5:continue
                    cm=move(chosen,3);om=move(opp,3);cl=[x.close for x in chosen[-20:]];aligned=chosen[-1].close>ema(cl,5)>ema(cl,13)
                    events.append(Event(d,spot.timestamp,direction,st,lookback,sim,depth,sep,cm,cm-om,vr(chosen),oi(chosen,3),aligned,chosen[-1].close,chosen[-1].high,(d,direction,st)))
    events.sort(key=lambda e:e.signal_time);return events,smap,tmap

def q(e,c):return e.lookback==c.lookback and e.similarity_atr<=c.max_similarity_atr and e.depth_atr>=c.min_depth_atr and e.separation>=c.min_separation and e.option_move_pct>=c.min_option_move_pct and e.outperformance_pct>=c.min_outperformance_pct and e.volume_ratio>=c.min_volume_ratio and e.oi_change_pct>=c.min_oi_change_pct and (not c.require_ema or e.ema_aligned) and e.premium>=c.min_premium and e.signal_time.time()<=time(c.end_hour,c.end_minute)
def sim(e,c,s,ts):
    start=bisect_right(ts,e.signal_time);tr=e.option_high+.10;dl=e.signal_time+timedelta(minutes=2);idx=-1;entry=0.0
    for i in range(start,len(s)):
        x=s[i]
        if x.timestamp.date()!=e.day or x.timestamp>dl:break
        if x.high>=tr:idx=i;entry=max(tr,x.open)+.20;break
    if idx<0:return None
    stop=max(.05,entry-c.stop_points);target=entry+c.target_points;cut=s[idx].timestamp+timedelta(minutes=c.max_hold_minutes);f=[x for x in s[idx:] if x.timestamp.date()==e.day and x.timestamp<=cut]
    if not f:return None
    for x in f:
        if x.low<=stop:return Trade(e.day,e.signal_time,e.direction,e.strike,'LOSS',-c.stop_points-.50)
        if x.high>=target:return Trade(e.day,e.signal_time,e.direction,e.strike,'WIN',c.target_points-.50)
    return Trade(e.day,e.signal_time,e.direction,e.strike,'TIMEOUT',f[-1].close-entry-.50)
def stats(t):
    t=tuple(t);n=len(t);w=sum(x.outcome=='WIN' for x in t);l=sum(x.outcome=='LOSS' for x in t);to=sum(x.outcome=='TIMEOUT' for x in t);net=sum(x.net_points for x in t);g=sum(max(0,x.net_points) for x in t);lv=abs(sum(min(0,x.net_points) for x in t));pf=g/lv if lv else(g if g else 0);eq=pk=dd=0
    for x in t:eq+=x.net_points;pk=max(pk,eq);dd=max(dd,pk-eq)
    return {'trades':n,'wins':w,'losses':l,'timeouts':to,'win_rate':round(100*w/n,2) if n else 0.0,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.0,'profit_factor':round(pf,2),'max_drawdown':round(dd,2)}
def evaluate(events,c,smap,tmap,start,end):
    by=defaultdict(list)
    for e in events:
        if start<=e.day<=end and q(e,c):by[e.day].append(e)
    out=[]
    for d in sorted(by):
        for e in by[d]:
            s=smap.get(e.series_key);ts=tmap.get(e.series_key)
            if not s or not ts:continue
            tr=sim(e,c,s,ts)
            if tr is not None:out.append(tr);break
    return stats(out),tuple(out)
def grid(selective=False):
    exits=((6.,4.,30),(8.,5.,35),(10.,6.,40)) if selective else ((3.,4.,20),(4.,5.,25))
    out=[]
    if selective:
        iterator=itertools.product((20,30),(.25,.40),(.8,1.2),(7,10),(.8,1.5),(1.2,2.0),(1.2,1.5),(0.,2.),(40.,60.),((11,0),(12,0)),exits)
    else:
        iterator=itertools.product((20,30),(.25,.45),(.5,.9),(5,8),(.3,.8),(.5,1.2),(1.0,1.3),(-10.,0.),(30.,50.),((11,30),(13,0)),exits)
    for v in iterator:out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],True,v[8],v[9][0],v[9][1],v[10][0],v[10][1],v[10][2]))
    return out
def rank(s,sel):
    minimum=10 if sel else 18
    if s['trades']<minimum or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
    p=s['wins']/s['trades'];z=1;lb=(p+z*z/(2*s['trades'])-z*math.sqrt((p*(1-p)+z*z/(4*s['trades']))/s['trades']))/(1+z*z/s['trades']);return lb*100+min(s['profit_factor'],4)*5+min(s['expectancy'],5)*(4 if sel else 2)-s['max_drawdown']/60
def mend(y,m):return date(y,12,31) if m==12 else date(y,m+1,1)-timedelta(days=1)
def walk(name,cands,events,smap,tmap,lo,sel):
    folds=[];oos=[]
    for y,m in ((2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)):
        a=date(y,m,1);b=mend(y,m);r=[]
        for c in cands:
            tr,_=evaluate(events,c,smap,tmap,lo,a-timedelta(days=1));sc=rank(tr,sel)
            if sc>-1e8:r.append((sc,c,tr))
        r.sort(key=lambda x:x[0],reverse=True)
        if not r:folds.append({'month':a.strftime('%Y-%m'),'status':'NO_TRAINING_CANDIDATE'});continue
        _,c,tr=r[0];te,tt=evaluate(events,c,smap,tmap,a,b);oos.extend(tt);folds.append({'month':a.strftime('%Y-%m'),'candidate_frozen_before_month':True,'candidate':asdict(c),'training':tr,'test':te,'trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat()} for x in tt]})
    ag=stats(oos);pos=sum(1 for f in folds if 'test'in f and f['test']['expectancy']>0);sixty=sum(1 for f in folds if 'test'in f and f['test']['win_rate']>=60);active=sum(1 for f in folds if 'test'in f and f['test']['trades']>0);avg=ag['trades']/active if active else 0
    if sel:passed=10<=ag['trades']<=28 and ag['win_rate']>=70 and ag['expectancy']>=1.5 and ag['profit_factor']>=1.5 and pos>=4 and sixty>=4 and avg<=4
    else:passed=ag['trades']>=20 and ag['win_rate']>=70 and ag['expectancy']>0 and ag['profit_factor']>=1.2 and pos>=4 and sixty>=4
    return {'name':name,'status':'PROVEN_70_PLUS' if passed else 'NO_70_PLUS_CANDIDATE_PROVEN','aggregate_oos':ag,'positive_months':pos,'sixty_plus_months':sixty,'active_months':active,'avg_trades_per_active_month':round(avg,2),'folds':folds}
def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(path);op=_parse_option_frame(path);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
    for r in op.itertuples(index=False):rows[r.day].append(r)
    events,smap,tmap=build_events(sbd,rows);accuracy=walk('MW Accuracy',grid(False),events,smap,tmap,lo,False);selective=walk('MW Selective Profit',grid(True),events,smap,tmap,lo,True);payload={'search_name':'Shiv M-W Neckline Monthly Walk-Forward','events_built':len(events),'accuracy':accuracy,'selective_profit':selective,'overall_status':'BOTH_PROVEN' if accuracy['status']=='PROVEN_70_PLUS' and selective['status']=='PROVEN_70_PLUS' else 'NOT_BOTH_PROVEN'};Path('strategy_70_mw.json').write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
