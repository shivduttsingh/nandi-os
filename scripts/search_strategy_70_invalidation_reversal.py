from __future__ import annotations
import itertools,json,sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass,asdict
from datetime import date,time,timedelta
from pathlib import Path
from statistics import mean,median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle
@dataclass(frozen=True)
class E:
 day:date;ts:object;orig:str;opp:str;strike:int;imp:float;outperf:float;vol:float;oi:float;orig_low:float;opp_high:float;orig_key:tuple;opp_key:tuple
@dataclass(frozen=True)
class C:
 min_imp:float;min_outperf:float;min_vol:float;min_oi:float;window:int;target:float;stop:float;cooldown:int
@dataclass(frozen=True)
class T:
 day:date;ts:object;entry_time:object;side:str;strike:int;outcome:str;net:float

def pct(a,b):return ((b/a)-1)*100 if a and a>0 else 0.
def atr(c,lb=14):
 if len(c)<2:return 0.
 s=c[-min(lb,len(c)-1):];p=c[-len(s)-1].close if len(c)>len(s) else c[0].close;v=[]
 for x in s:v.append(max(x.high-x.low,abs(x.high-p),abs(x.low-p)));p=x.close
 return mean(v) if v else 0.
def vr(c):
 h=[x.volume for x in c[-13:-1] if x.volume and x.volume>0]
 if not h:return 0.
 b=median(h);return c[-1].volume/b if b>0 else 0.
def move(c,n=3):return pct(c[-n-1].close,c[-1].close) if len(c)>=n+1 else 0.
def oi(c,n=3):
 if len(c)<n+1:return 0.
 o=c[-n-1].open_interest;return pct(o,c[-1].open_interest) if o and o>0 else 0.

def build():
 p=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(p);op=_parse_option_frame(p);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)]
 sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};ors=defaultdict(list)
 for r in op.itertuples(index=False):ors[r.day].append(r)
 ev=[];smap={};tmap={}
 for d in sorted(sbd):
  ds=sbd[d];raw=ors.get(d,[])
  if len(ds)<100 or not raw:continue
  at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
  for r in raw:
   side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
   if not side:continue
   st=int(r.strike);cc=_row_to_candle(r);at[r.timestamp].append((side,st,cc));lists[(side,st)].append(cc);strikes[side].add(st)
  if not(strikes['CE']&strikes['PE']):continue
  for k,v in lists.items():
   key=(d,k[0],k[1]);seq=tuple(sorted(v,key=lambda x:x.timestamp));smap[key]=seq;tmap[key]=[x.timestamp for x in seq]
  hist=defaultdict(list);sh=[]
  for x in ds:
   sh.append(x)
   for side,st,cc in at.get(x.timestamp,[]):hist[(side,st)].append(cc)
   if not(time(9,35)<=x.timestamp.time()<=time(14,0)) or len(sh)<25:continue
   a=atr(sh[-25:]);st=_nearest_common_strike(strikes,x.close)
   if a<=0 or st is None:continue
   rawimp=(sh[-1].close-sh[-4].close)/a if len(sh)>=4 else 0.
   if abs(rawimp)<.4:continue
   orig='CE' if rawimp>0 else 'PE';opp='PE' if orig=='CE' else 'CE';ch=hist.get((orig,st),[]);oh=hist.get((opp,st),[])
   if len(ch)<14 or len(oh)<14:continue
   cm=move(ch,3);om=move(oh,3)
   ev.append(E(d,x.timestamp,orig,opp,st,abs(rawimp),cm-om,vr(ch),oi(ch,3),ch[-1].low,oh[-1].high,(d,orig,st),(d,opp,st)))
 ev.sort(key=lambda z:z.ts);return ev,smap,tmap,sorted(sbd)

def q(e,c):return e.imp>=c.min_imp and e.outperf>=c.min_outperf and e.vol>=c.min_vol and e.oi>=c.min_oi

def sim(e,c,smap,tmap):
 os=smap.get(e.orig_key);ot=tmap.get(e.orig_key);ps=smap.get(e.opp_key);pt=tmap.get(e.opp_key)
 if not os or not ps:return None
 oi0=bisect_right(ot,e.ts);pi0=bisect_right(pt,e.ts);cut=e.ts+timedelta(minutes=c.window);orig_invalid=False;entry_idx=-1;entry=0.
 # walk opposite series timestamps; by each timestamp also inspect latest original candle
 for i in range(pi0,len(ps)):
  p=ps[i]
  if p.timestamp.date()!=e.day or p.timestamp>cut:break
  j=bisect_right(ot,p.timestamp)-1
  if j>=oi0 and os[j].low<=e.orig_low-.05:orig_invalid=True
  trigger=e.opp_high+.10
  if orig_invalid and p.high>=trigger:
   entry_idx=i;entry=max(trigger,p.open)+.20;break
 if entry_idx<0:return None
 sl=max(.05,entry-c.stop);tp=entry+c.target;et=ps[entry_idx].timestamp;end=et+timedelta(minutes=20);f=[]
 for x in ps[entry_idx:]:
  if x.timestamp.date()!=e.day or x.timestamp>end:break
  f.append(x)
 if not f:return None
 for x in f:
  if x.low<=sl:return T(e.day,e.ts,et,e.opp,e.strike,'LOSS',-c.stop-.50)
  if x.high>=tp:return T(e.day,e.ts,et,e.opp,e.strike,'WIN',c.target-.50)
 return T(e.day,e.ts,et,e.opp,e.strike,'TIMEOUT',f[-1].close-entry-.50)

def stats(trades,days):
 n=len(trades);w=sum(t.outcome=='WIN' for t in trades);net=sum(t.net for t in trades);g=sum(max(0,t.net) for t in trades);lv=abs(sum(min(0,t.net) for t in trades));pf=g/lv if lv else(g if g else 0.);dc=defaultdict(int);dn=defaultdict(float);eq=pk=dd=0.
 for t in sorted(trades,key=lambda z:z.entry_time):dc[t.day]+=1;dn[t.day]+=t.net;eq+=t.net;pk=max(pk,eq);dd=max(dd,pk-eq)
 nd=len(days);return {'trades':n,'wins':w,'losses':n-w,'win_rate':round(100*w/n,2) if n else 0.,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.,'profit_factor':round(pf,2),'max_drawdown':round(dd,2),'trading_days':nd,'avg_trades_per_day':round(n/nd,2) if nd else 0.,'avg_net_points_per_day':round(net/nd,2) if nd else 0.,'pct_days_with_3plus_trades':round(100*sum(dc[d]>=3 for d in days)/nd,2) if nd else 0.,'pct_profitable_days':round(100*sum(dn[d]>0 for d in days)/nd,2) if nd else 0.}
def evaluate(ev,c,smap,tmap,days,a,b):
 by=defaultdict(list)
 for e in ev:
  if a<=e.day<=b and q(e,c):by[e.day].append(e)
 out=[]
 for d in [x for x in days if a<=x<=b]:
  last=None;cnt=0
  for e in by.get(d,[]):
   if cnt>=5:break
   if last is not None and (e.ts-last).total_seconds()<c.cooldown*60:continue
   t=sim(e,c,smap,tmap)
   if t is not None:out.append(t);last=t.entry_time;cnt+=1
 return stats(out,[x for x in days if a<=x<=b]),tuple(out)
def grid():
 for v in itertools.product((.6,.9,1.2),(.3,.7,1.1),(.8,1.2),(-10.,0.),(3,5,8),((6.,3.),(8.,4.),(10.,5.)),(5,8)):
  yield C(v[0],v[1],v[2],v[3],v[4],v[5][0],v[5][1],v[6])
def score(s):
 if s['trades']<250 or s['avg_trades_per_day']<2.5 or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
 return s['win_rate']*1.5+s['avg_net_points_per_day']*1.5+min(s['profit_factor'],4)*5+s['pct_profitable_days']*.15-s['max_drawdown']/120

def main():
 ev,smap,tmap,days=build();train=(date(2025,7,1),date(2025,12,31));val=(date(2026,1,1),date(2026,3,31));stress=(date(2026,4,1),date(2026,6,30));rank=[];fallback=[]
 for c in grid():
  s,_=evaluate(ev,c,smap,tmap,days,*train);sc=score(s);fallback.append((s['win_rate']+max(s['expectancy'],0)*2+s['avg_trades_per_day'],c,s));
  if sc>-1e8:rank.append((sc,c,s))
 if rank:rank.sort(key=lambda z:z[0],reverse=True);_,c,tr=rank[0]
 else:fallback.sort(key=lambda z:z[0],reverse=True);_,c,tr=fallback[0]
 vs,vt=evaluate(ev,c,smap,tmap,days,*val);ss,st=evaluate(ev,c,smap,tmap,days,*stress);vp=vs['trades']>=150 and 3<=vs['avg_trades_per_day']<=5 and vs['win_rate']>=70 and vs['expectancy']>=3.5 and vs['profit_factor']>=2 and vs['avg_net_points_per_day']>=15 and vs['pct_profitable_days']>=70;sp=ss['trades']>=125 and ss['avg_trades_per_day']>=2.5 and ss['win_rate']>=65 and ss['expectancy']>0 and ss['profit_factor']>=1.5 and ss['avg_net_points_per_day']>=10
 out={'search_name':'Shiv Invalidated Momentum Reversal Proof','candidate_count':len(list(grid())),'events_built':len(ev),'status':'PROVEN_70_PLUS_DAILY' if vp and sp else 'NO_70_PLUS_DAILY_PROOF','candidate':asdict(c),'training':tr,'validation':vs,'stress':ss,'validation_pass':vp,'stress_pass':sp,'proof_rule':'Jan-Mar >=150 trades, 3-5/day, >=70% target wins, expectancy>=3.5, PF>=2, >=15 net option points/day, >=70% profitable days; Apr-Jun stress >=65% and positive economics.','execution':'Original momentum must first invalidate through its option low, then opposite ATM option must break its signal high within frozen window; +0.20 entry slippage, 0.50 friction, stop-first ambiguity, max 5/day.','validation_trades':[{**asdict(x),'day':x.day.isoformat(),'ts':x.ts.isoformat(),'entry_time':x.entry_time.isoformat()} for x in vt]};Path('strategy_70_invalidation_reversal.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
