from __future__ import annotations
import itertools,json,sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass,asdict
from datetime import date,time,timedelta
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle
@dataclass(frozen=True)
class E:
 day:date;ts:object;side:str;strike:int;period:int;level:int;rsi_prev:float;rsi_now:float;opt_move:float;outperf:float;vol:float;oi:float;high:float;key:tuple
@dataclass(frozen=True)
class C:
 period:int;level:int;min_opt_move:float;min_outperf:float;min_vol:float;min_oi:float;target:float;stop:float;cooldown:int
@dataclass(frozen=True)
class T:
 day:date;ts:object;entry_time:object;side:str;strike:int;outcome:str;net:float

def pct(a,b):return ((b/a)-1)*100 if a and a>0 else 0.
def move(c,n=2):return pct(c[-n-1].close,c[-1].close) if len(c)>=n+1 else 0.
def vr(c):
 h=[x.volume for x in c[-13:-1] if x.volume and x.volume>0]
 if not h:return 0.
 b=median(h);return c[-1].volume/b if b>0 else 0.
def oi(c,n=3):
 if len(c)<n+1:return 0.
 o=c[-n-1].open_interest;return pct(o,c[-1].open_interest) if o and o>0 else 0.
def rsi(vals,p):
 if len(vals)<p+2:return None
 ds=[vals[i]-vals[i-1] for i in range(len(vals)-p,len(vals))];g=sum(max(x,0) for x in ds)/p;l=sum(max(-x,0) for x in ds)/p
 if l==0:return 100.
 rs=g/l;return 100-100/(1+rs)

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
  hist=defaultdict(list);cl=[]
  for x in ds:
   cl.append(x.close)
   for side,st,cc in at.get(x.timestamp,[]):hist[(side,st)].append(cc)
   if not(time(9,35)<=x.timestamp.time()<=time(14,15)):continue
   st=_nearest_common_strike(strikes,x.close)
   if st is None:continue
   for p in (5,7,14):
    if len(cl)<p+3:continue
    prev=rsi(cl[:-1],p);now=rsi(cl,p)
    if prev is None or now is None:continue
    for level in (25,30,35):
     side='CE' if prev<=level and now>level else 'PE' if prev>=100-level and now<100-level else ''
     if not side:continue
     opp='PE' if side=='CE' else 'CE';ch=hist.get((side,st),[]);oh=hist.get((opp,st),[])
     if len(ch)<14 or len(oh)<14:continue
     cm,om=move(ch,2),move(oh,2);ev.append(E(d,x.timestamp,side,st,p,level,prev,now,cm,cm-om,vr(ch),oi(ch,3),ch[-1].high,(d,side,st)))
 ev.sort(key=lambda z:z.ts);return ev,smap,tmap,sorted(sbd)
def q(e,c):return e.period==c.period and e.level==c.level and e.opt_move>=c.min_opt_move and e.outperf>=c.min_outperf and e.vol>=c.min_vol and e.oi>=c.min_oi
def sim(e,c,smap,tmap):
 s=smap.get(e.key);ts=tmap.get(e.key)
 if not s or not ts:return None
 i=bisect_right(ts,e.ts);tr=e.high+.10;dl=e.ts+timedelta(minutes=2);idx=-1;entry=0.
 for j in range(i,len(s)):
  x=s[j]
  if x.timestamp.date()!=e.day or x.timestamp>dl:break
  if x.high>=tr:idx=j;entry=max(tr,x.open)+.20;break
 if idx<0:return None
 sl=max(.05,entry-c.stop);tp=entry+c.target;et=s[idx].timestamp;cut=et+timedelta(minutes=20);f=[]
 for x in s[idx:]:
  if x.timestamp.date()!=e.day or x.timestamp>cut:break
  f.append(x)
 if not f:return None
 for x in f:
  if x.low<=sl:return T(e.day,e.ts,et,e.side,e.strike,'LOSS',-c.stop-.50)
  if x.high>=tp:return T(e.day,e.ts,et,e.side,e.strike,'WIN',c.target-.50)
 return T(e.day,e.ts,et,e.side,e.strike,'TIMEOUT',f[-1].close-entry-.50)
def stats(t,days):
 n=len(t);w=sum(x.outcome=='WIN' for x in t);net=sum(x.net for x in t);g=sum(max(0,x.net) for x in t);lv=abs(sum(min(0,x.net) for x in t));pf=g/lv if lv else(g if g else 0.);dc=defaultdict(int);dn=defaultdict(float);eq=pk=dd=0.
 for x in sorted(t,key=lambda z:z.entry_time):dc[x.day]+=1;dn[x.day]+=x.net;eq+=x.net;pk=max(pk,eq);dd=max(dd,pk-eq)
 nd=len(days);return {'trades':n,'wins':w,'losses':n-w,'win_rate':round(100*w/n,2) if n else 0.,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.,'profit_factor':round(pf,2),'max_drawdown':round(dd,2),'trading_days':nd,'avg_trades_per_day':round(n/nd,2) if nd else 0.,'avg_net_points_per_day':round(net/nd,2) if nd else 0.,'pct_days_with_3plus_trades':round(100*sum(dc[d]>=3 for d in days)/nd,2) if nd else 0.,'pct_profitable_days':round(100*sum(dn[d]>0 for d in days)/nd,2) if nd else 0.}
def eval(ev,c,smap,tmap,days,a,b):
 out=[];last=defaultdict(lambda:None);cnt=defaultdict(int)
 for e in ev:
  if not(a<=e.day<=b) or not q(e,c) or cnt[e.day]>=5:continue
  if last[e.day] is not None and (e.ts-last[e.day]).total_seconds()<c.cooldown*60:continue
  t=sim(e,c,smap,tmap)
  if t is not None:out.append(t);last[e.day]=t.entry_time;cnt[e.day]+=1
 ds=[d for d in days if a<=d<=b];return stats(out,ds),tuple(out)
def grid():
 for v in itertools.product((5,7,14),(25,30,35),(0.,.3),(.2,.6),(.8,1.2),(-10.,0.),((6.,3.),(8.,4.),(10.,5.)),(4,7)):
  yield C(v[0],v[1],v[2],v[3],v[4],v[5],v[6][0],v[6][1],v[7])
def score(s):
 if s['trades']<250 or s['avg_trades_per_day']<2.5 or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
 return s['win_rate']*1.5+s['avg_net_points_per_day']*1.6+min(s['profit_factor'],4)*5+s['pct_profitable_days']*.15-s['max_drawdown']/120

def main():
 ev,smap,tmap,days=build();train=(date(2025,7,1),date(2025,12,31));val=(date(2026,1,1),date(2026,3,31));stress=(date(2026,4,1),date(2026,6,30));good=[];fall=[]
 for c in grid():
  s,_=eval(ev,c,smap,tmap,days,*train);sc=score(s);fall.append((s['win_rate']+max(s['expectancy'],0)*2+s['avg_trades_per_day'],c,s));
  if sc>-1e8:good.append((sc,c,s))
 if good:good.sort(key=lambda z:z[0],reverse=True);_,c,tr=good[0]
 else:fall.sort(key=lambda z:z[0],reverse=True);_,c,tr=fall[0]
 vs,vt=eval(ev,c,smap,tmap,days,*val);ss,st=eval(ev,c,smap,tmap,days,*stress);vp=vs['trades']>=150 and 3<=vs['avg_trades_per_day']<=5 and vs['win_rate']>=70 and vs['expectancy']>=3.5 and vs['profit_factor']>=2 and vs['avg_net_points_per_day']>=15 and vs['pct_profitable_days']>=70;sp=ss['trades']>=125 and ss['avg_trades_per_day']>=2.5 and ss['win_rate']>=65 and ss['expectancy']>0 and ss['profit_factor']>=1.5 and ss['avg_net_points_per_day']>=10
 out={'search_name':'Shiv RSI Extreme-Reclaim + Option Confirmation Proof','candidate_count':len(list(grid())),'events_built':len(ev),'status':'PROVEN_70_PLUS_DAILY' if vp and sp else 'NO_70_PLUS_DAILY_PROOF','candidate':asdict(c),'training':tr,'validation':vs,'stress':ss,'validation_pass':vp,'stress_pass':sp,'proof_rule':'Jan-Mar >=150 trades, 3-5/day, >=70% wins, expectancy>=3.5, PF>=2, >=15 net option points/day, >=70% profitable days; Apr-Jun stress >=65% and positive economics.','execution':'NIFTY RSI crosses back from extreme; ATM option must confirm premium move/outperformance/volume/OI; buy-stop next 2 minutes; +0.20 slippage +0.50 friction; max 5/day.'};Path('strategy_70_rsi_reclaim.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
