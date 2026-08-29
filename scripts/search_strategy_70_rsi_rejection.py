from __future__ import annotations

import itertools,json,math,sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict,dataclass
from datetime import date,datetime,time,timedelta
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle

@dataclass(frozen=True)
class Event:
    day:date;signal_time:datetime;direction:str;strike:int;rsi:float;option_outperf:float;volume_ratio:float;premium:float;option_high:float;series_key:tuple[date,str,int]
@dataclass(frozen=True)
class Config:
    period:int;lower:float;upper:float;min_outperf:float;min_volume:float;min_premium:float;target:float;stop:float;cooldown:int;max_trades_day:int=5
@dataclass(frozen=True)
class Trade:
    day:date;signal_time:datetime;entry_time:datetime;direction:str;strike:int;outcome:str;net_points:float

def rsi(closes,period):
    if len(closes)<period+1:return None
    gains=losses=0.0
    for a,b in zip(closes[-period-1:-1],closes[-period:]):
        d=b-a
        if d>0:gains+=d
        else:losses-=d
    if losses==0:return 100.0
    rs=(gains/period)/(losses/period);return 100-100/(1+rs)
def pct(a,b):return ((b/a)-1)*100 if a>0 else 0.0
def vr(hist):
    if len(hist)<9:return 0.0
    h=[x.volume for x in hist[-9:-1] if x.volume>0]
    if not h:return 0.0
    m=median(h);return hist[-1].volume/m if m>0 else 0.0

def build_events(sbd,rows,period,lower,upper):
    ev=[];sm={};tm={}
    for d in sorted(sbd):
        spot=sbd[d];raw=rows.get(d,[])
        if len(spot)<80 or not raw:continue
        at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
        for r in raw:
            side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
            if not side:continue
            k=int(r.strike);c=_row_to_candle(r);at[r.timestamp].append((side,k,c));lists[(side,k)].append(c);strikes[side].add(k)
        if not(strikes['CE']&strikes['PE']):continue
        for (side,k),vals in lists.items():
            sk=(d,side,k);s=tuple(sorted(vals,key=lambda x:x.timestamp));sm[sk]=s;tm[sk]=[x.timestamp for x in s]
        hist=defaultdict(list);cl=[];prev_r=None
        for c in spot:
            cl.append(c.close)
            for side,k,oc in at.get(c.timestamp,[]):hist[(side,k)].append(oc)
            cur=rsi(cl,period)
            if cur is None:continue
            if not(time(9,25)<=c.timestamp.time()<=time(14,30)):prev_r=cur;continue
            direction=''
            if prev_r is not None and prev_r<=lower and cur>lower and c.close>c.open:direction='CE'
            elif prev_r is not None and prev_r>=upper and cur<upper and c.close<c.open:direction='PE'
            prev_r=cur
            if not direction:continue
            k=_nearest_common_strike(strikes,c.close)
            if k is None:continue
            chosen=hist.get((direction,k),[]);opp=hist.get(('PE' if direction=='CE' else 'CE',k),[])
            if len(chosen)<5 or len(opp)<5:continue
            out=pct(chosen[-4].close,chosen[-1].close)-pct(opp[-4].close,opp[-1].close)
            ev.append(Event(d,c.timestamp,direction,k,cur,out,vr(chosen),chosen[-1].close,chosen[-1].high,(d,direction,k)))
    ev.sort(key=lambda x:x.signal_time);return ev,sm,tm

def sim(e,c,s,t):
    if not s or not t:return None
    start=bisect_right(t,e.signal_time);deadline=e.signal_time+timedelta(minutes=2);idx=-1;entry=0
    for i in range(start,len(s)):
        b=s[i]
        if b.timestamp.date()!=e.day or b.timestamp>deadline:break
        if b.high>=e.option_high+.1:idx=i;entry=max(e.option_high+.1,b.open)+.2;break
    if idx<0:return None
    sl=entry-c.stop;tg=entry+c.target;et=s[idx].timestamp;future=[x for x in s[idx:] if x.timestamp.date()==e.day and x.timestamp<=et+timedelta(minutes=20)]
    for b in future:
        if b.low<=sl:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'LOSS',-c.stop-.5)
        if b.high>=tg:return Trade(e.day,e.signal_time,et,e.direction,e.strike,'WIN',c.target-.5)
    return Trade(e.day,e.signal_time,et,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-.5) if future else None
def stats(t):
    t=tuple(t);n=len(t);w=sum(x.outcome=='WIN' for x in t);l=sum(x.outcome=='LOSS' for x in t);net=sum(x.net_points for x in t);g=sum(max(0,x.net_points) for x in t);lv=abs(sum(min(0,x.net_points) for x in t));pf=g/lv if lv else (g if g else 0);return {'trades':n,'wins':w,'losses':l,'timeouts':n-w-l,'win_rate':round(100*w/n,2) if n else 0,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0,'profit_factor':round(pf,2)}
def daily(t,days,a,b):
    act=[d for d in days if a<=d<=b];cnt=defaultdict(int);net=defaultdict(float)
    for x in t:cnt[x.day]+=1;net[x.day]+=x.net_points
    n=len(act);return {'trading_days':n,'avg_trades_per_day':round(len(t)/n,2) if n else 0,'avg_net_points_per_day':round(sum(net.values())/n,2) if n else 0,'pct_days_with_3plus_trades':round(100*sum(cnt[d]>=3 for d in act)/n,2) if n else 0,'pct_profitable_days':round(100*sum(net[d]>0 for d in act)/n,2) if n else 0}
def evaluate(ev,c,sm,tm,days,a,b):
    by=defaultdict(list)
    for e in ev:
        if a<=e.day<=b and e.option_outperf>=c.min_outperf and e.volume_ratio>=c.min_volume and e.premium>=c.min_premium:by[e.day].append(e)
    out=[]
    for d in [x for x in days if a<=x<=b]:
        last=None
        for e in by.get(d,[]):
            if sum(x.day==d for x in out)>=c.max_trades_day:break
            if last is not None and (e.signal_time-last).total_seconds()<c.cooldown*60:continue
            tr=sim(e,c,sm.get(e.series_key),tm.get(e.series_key))
            if tr:out.append(tr);last=tr.entry_time
    return stats(out),daily(out,days,a,b),out
def exact(p):
    s,d=p['stats'],p['daily'];return d['trading_days']>=55 and 3<=d['avg_trades_per_day']<=5 and d['pct_days_with_3plus_trades']>=65 and s['win_rate']>=70 and s['expectancy']>=3.5 and s['profit_factor']>=2 and d['avg_net_points_per_day']>=15 and d['pct_profitable_days']>=70

def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(path);op=_parse_option_frame(path);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
    for r in op.itertuples(index=False):rows[r.day].append(r)
    days=sorted(sbd);train=(date(2025,7,1),date(2025,12,31));valw=(date(2026,1,1),date(2026,3,31));stressw=(date(2026,4,1),date(2026,6,30));ranked=[];near=[];cache={};count=0
    for period,lo_r,hi_r in ((7,30,70),(7,35,65),(14,24,78),(14,30,70)):
        ev,sm,tm=build_events(sbd,rows,period,lo_r,hi_r);cache[(period,lo_r,hi_r)]=(ev,sm,tm)
        for outp,vol,minp,pair,cd in itertools.product((0,.3,.7),(.8,1.0,1.2),(30.,50.),((6.,3.),(8.,4.),(10.,5.)),(5,8)):
            c=Config(period,lo_r,hi_r,outp,vol,minp,pair[0],pair[1],cd);st,ds,_=evaluate(ev,c,sm,tm,days,*train);count+=1;raw=st['win_rate']+st['profit_factor']*3+st['expectancy']*2+ds['avg_trades_per_day']*5+ds['avg_net_points_per_day'];near.append((raw,c,st,ds))
            if ds['avg_trades_per_day']>=1.5 and st['expectancy']>0 and st['profit_factor']>1:ranked.append((raw,c,st,ds))
    near.sort(key=lambda x:x[0],reverse=True);ranked.sort(key=lambda x:x[0],reverse=True);best={'config':asdict(near[0][1]),'stats':near[0][2],'daily':near[0][3]} if near else None
    if not ranked:out={'search_name':'Shiv RSI Rejection + Option Confirmation Proof','status':'NO_TRAINING_CANDIDATE','config_count':count,'best_available_training':best}
    else:
        _,c,trst,trds=ranked[0];ev,sm,tm=cache[(c.period,c.lower,c.upper)];def pp(a,b):
            st,ds,tr=evaluate(ev,c,sm,tm,days,a,b);return {'stats':st,'daily':ds,'trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat(),'entry_time':x.entry_time.isoformat()} for x in tr]}
        val=pp(*valw);stress=pp(*stressw);comb=pp(valw[0],stressw[1]);proven=exact(val) and exact(stress) and comb['stats']['win_rate']>=70 and comb['daily']['avg_net_points_per_day']>=15;out={'search_name':'Shiv RSI Rejection + Option Confirmation Proof','status':'PROVEN_EXACT_TARGET' if proven else 'NO_EXACT_TARGET_PROVEN','config_count':count,'chosen_config':asdict(c),'training':{'stats':trst,'daily':trds},'validation':val,'stress':stress,'combined_oos':comb,'best_available_training':best}
    Path('strategy_70_rsi_rejection.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
