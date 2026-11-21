from __future__ import annotations
import itertools,json,sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass,asdict
from datetime import date,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import search_strategy_70_multisetup as m
from test1.public_backtest import _download_public_sample,_parse_option_frame,_parse_spot_frame,_row_to_candle
@dataclass(frozen=True)
class C:
 il:int;pl:int;rl:int;min_score:float;initial_stop:float;arm:float;lock:float;runner:float;hold:int;cooldown:int;quota:int=3
@dataclass(frozen=True)
class T:
 day:date;family:str;signal_time:object;entry_time:object;direction:str;strike:int;outcome:str;net_points:float;armed:bool;runner_hit:bool

def entry_and_series(w,maps):
 e=w.event;s,ts=maps[w.family][0].get(e.series_key),maps[w.family][1].get(e.series_key)
 if not s or not ts:return None
 start=bisect_right(ts,e.signal_time);tr=e.option_high+.10;dl=e.signal_time+timedelta(minutes=2);idx=-1;entry=0.
 for i in range(start,len(s)):
  x=s[i]
  if x.timestamp.date()!=e.day or x.timestamp>dl:break
  if x.high>=tr:idx=i;entry=max(tr,x.open)+.20;break
 return (s,idx,entry) if idx>=0 else None

def sim(w,c,maps):
 z=entry_and_series(w,maps)
 if z is None:return None
 s,idx,entry=z;e=w.event;et=s[idx].timestamp;cut=et+timedelta(minutes=c.hold);initial=max(.05,entry-c.initial_stop);lockpx=entry+c.lock;armpx=entry+c.arm;runpx=entry+c.runner;armed=False;last=entry
 for x in s[idx:]:
  if x.timestamp.date()!=e.day or x.timestamp>cut:break
  last=x.close
  if not armed:
   if x.low<=initial:return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'LOSS',-c.initial_stop-.50,False,False)
   if x.high>=armpx:
    armed=True
    # Conservative same-bar handling once the lock is armed.
    if x.low<=lockpx:return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'LOCK',c.lock-.50,True,False)
    if x.high>=runpx:return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'RUNNER',c.runner-.50,True,True)
  else:
   if x.low<=lockpx:return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'LOCK',c.lock-.50,True,False)
   if x.high>=runpx:return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'RUNNER',c.runner-.50,True,True)
 return T(e.day,w.family,e.signal_time,et,e.direction,e.strike,'TIMEOUT',last-entry-.50,armed,False)

def wrapped(c,all_events,a,b):
 by=defaultdict(list)
 for fam,events in all_events.items():
  for e in events:
   if not(a<=e.day<=b):continue
   sc=m.impulse_pass_score(e,c.il) if fam=='IMPULSE' else m.pullback_pass_score(e,c.pl) if fam=='PULLBACK' else m.reversal_pass_score(e,c.rl)
   if sc is not None and sc>=c.min_score:by[e.day].append(m.Wrapped(fam,e,sc))
 return by

def stats(t,days):
 n=len(t);wins=sum(x.net_points>0 for x in t);net=sum(x.net_points for x in t);g=sum(max(0,x.net_points) for x in t);lv=abs(sum(min(0,x.net_points) for x in t));pf=g/lv if lv else(g if g else 0.);dc=defaultdict(int);dn=defaultdict(float);eq=pk=dd=0.;locks=sum(x.outcome=='LOCK' for x in t);runs=sum(x.runner_hit for x in t)
 for x in sorted(t,key=lambda z:z.entry_time):dc[x.day]+=1;dn[x.day]+=x.net_points;eq+=x.net_points;pk=max(pk,eq);dd=max(dd,pk-eq)
 nd=len(days);return {'trades':n,'profitable_trades':wins,'loss_or_flat_trades':n-wins,'win_rate':round(100*wins/n,2) if n else 0.,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.,'profit_factor':round(pf,2),'max_drawdown':round(dd,2),'lock_exits':locks,'runner_hits':runs,'runner_hit_rate':round(100*runs/n,2) if n else 0.,'trading_days':nd,'avg_trades_per_day':round(n/nd,2) if nd else 0.,'avg_net_points_per_day':round(net/nd,2) if nd else 0.,'pct_days_with_3plus_trades':round(100*sum(dc[d]>=3 for d in days)/nd,2) if nd else 0.,'pct_profitable_days':round(100*sum(dn[d]>0 for d in days)/nd,2) if nd else 0.,'pct_days_15plus_points':round(100*sum(dn[d]>=15 for d in days)/nd,2) if nd else 0.}
def evaluate(c,events,maps,days,a,b):
 by=wrapped(c,events,a,b);out=[]
 for d in [x for x in days if a<=x<=b]:
  arr=sorted(by.get(d,[]),key=lambda w:w.event.signal_time);last=None;fc=defaultdict(int);seen=set()
  for w in arr:
   if len([x for x in out if x.day==d])>=5:break
   key=(w.event.signal_time,w.event.direction,w.event.strike)
   if key in seen or fc[w.family]>=c.quota:continue
   if last is not None and (w.event.signal_time-last).total_seconds()<c.cooldown*60:continue
   t=sim(w,c,maps)
   if t is not None:out.append(t);last=t.entry_time;fc[w.family]+=1;seen.add(key)
 ds=[x for x in days if a<=x<=b];return stats(out,ds),tuple(out)
def grid():
 for v in itertools.product((0,1),(0,1),(0,1),(.4,.5,.6),(4.,5.),(2.,3.,4.),(1.,1.5,2.),(12.,18.,25.),(25,35),(5,8)):
  if v[6]>=v[5]:continue
  yield C(*v)
def score(s):
 if s['trades']<280 or s['avg_trades_per_day']<2.5 or s['win_rate']<55 or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
 return s['win_rate']*1.5+s['avg_net_points_per_day']*2+min(s['profit_factor'],4)*5+s['pct_profitable_days']*.15+s['runner_hit_rate']*.1-s['max_drawdown']/180

def main():
 p=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(p);op=_parse_option_frame(p);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
 for r in op.itertuples(index=False):rows[r.day].append(r)
 ie,ism,itm=m.imp.build_events(sbd,rows);pe,psm,ptm=m.pull.build_events(sbd,rows);re,rsm,rtm=m.rev.build_events(sbd,rows);events={'IMPULSE':ie,'PULLBACK':pe,'REVERSAL':re};maps={'IMPULSE':(ism,itm),'PULLBACK':(psm,ptm),'REVERSAL':(rsm,rtm)};days=sorted(sbd);train=(date(2025,7,1),date(2025,12,31));val=(date(2026,1,1),date(2026,3,31));stress=(date(2026,4,1),date(2026,6,30));rank=[];fallback=[];count=0
 for c in grid():
  count+=1;s,_=evaluate(c,events,maps,days,*train);sc=score(s);fallback.append((s['win_rate']+max(s['expectancy'],0)*3+s['avg_trades_per_day'],c,s));
  if sc>-1e8:rank.append((sc,c,s))
 if rank:rank.sort(key=lambda z:z[0],reverse=True);_,c,tr=rank[0]
 else:fallback.sort(key=lambda z:z[0],reverse=True);_,c,tr=fallback[0]
 vs,vt=evaluate(c,events,maps,days,*val);ss,st=evaluate(c,events,maps,days,*stress);vp=vs['trades']>=150 and 3<=vs['avg_trades_per_day']<=5 and vs['win_rate']>=70 and vs['expectancy']>=3 and vs['profit_factor']>=1.7 and vs['avg_net_points_per_day']>=15 and vs['pct_profitable_days']>=70;sp=ss['trades']>=125 and ss['avg_trades_per_day']>=2.5 and ss['win_rate']>=65 and ss['expectancy']>0 and ss['profit_factor']>=1.4 and ss['avg_net_points_per_day']>=10
 out={'search_name':'Shiv Profit-Lock + Runner Daily Proof','candidate_count':count,'status':'PROVEN_70_PLUS_DAILY' if vp and sp else 'NO_70_PLUS_DAILY_PROOF','candidate':asdict(c),'training':tr,'validation':vs,'stress':ss,'validation_pass':vp,'stress_pass':sp,'proof_rule':'Win means actual net-positive trade after friction. Jan-Mar >=150 trades, 3-5/day, >=70% profitable trades, expectancy>=3 option points/trade, PF>=1.7, >=15 net option points/day, >=70% profitable days; Apr-Jun stress >=65% wins and positive economics.','execution':'ATM option buy-stop after completed signal; initial stop then profit arm moves stop above entry; runner remains for 12-25 points; +0.20 slippage +0.50 friction; conservative stop-first handling; max 5/day.','validation_trades':[{**asdict(x),'day':x.day.isoformat(),'signal_time':x.signal_time.isoformat(),'entry_time':x.entry_time.isoformat()} for x in vt]};Path('strategy_70_profit_lock.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
