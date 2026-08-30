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
 day:date;ts:object;strike:int;combo:float;move1:float;move3:float;volratio:float;compression:float;spotmove:float;key:tuple
@dataclass(frozen=True)
class C:
 min_move1:float;min_move3:float;min_vol:float;max_compression:float;min_spotmove:float;target:float;stop:float;hold:int;cooldown:int
@dataclass(frozen=True)
class T:
 day:date;ts:object;entry_time:object;strike:int;outcome:str;net:float

def pct(a,b):return ((b/a)-1)*100 if a and a>0 else 0.
def build():
 p=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(p);op=_parse_option_frame(p);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)]
 sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};ors=defaultdict(list)
 for r in op.itertuples(index=False):ors[r.day].append(r)
 ev=[];combo_map={};time_map={}
 for d in sorted(sbd):
  ds=sbd[d];raw=ors.get(d,[])
  if len(ds)<100 or not raw:continue
  at=defaultdict(dict);strikes={'CE':set(),'PE':set()}
  for r in raw:
   side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
   if not side:continue
   st=int(r.strike);at[r.timestamp][(side,st)]=_row_to_candle(r);strikes[side].add(st)
  common=strikes['CE']&strikes['PE']
  if not common:continue
  for st in common:
   seq=[]
   for ts,mp in sorted(at.items()):
    ce=mp.get(('CE',st));pe=mp.get(('PE',st))
    if ce and pe:seq.append((ts,ce.open+pe.open,ce.close+pe.close,ce.volume+pe.volume))
   if seq:combo_map[(d,st)]=seq;time_map[(d,st)]=[x[0] for x in seq]
  spotcl=[];chist=defaultdict(list)
  for x in ds:
   spotcl.append(x.close);st=_nearest_common_strike(strikes,x.close)
   if st is None:continue
   mp=at.get(x.timestamp,{});ce=mp.get(('CE',st));pe=mp.get(('PE',st))
   if ce and pe:chist[st].append((x.timestamp,ce.close+pe.close,ce.volume+pe.volume))
   h=chist.get(st,[])
   if not(time(9,35)<=x.timestamp.time()<=time(14,10)) or len(h)<14 or len(spotcl)<5:continue
   vals=[z[1] for z in h];vols=[z[2] for z in h];cur=vals[-1];m1=pct(vals[-2],cur);m3=pct(vals[-4],cur);base=median(vols[-13:-1]);vr=vols[-1]/base if base>0 else 0.;recent=vals[-10:-1];comp=100*(max(recent)-min(recent))/max(median(recent),.05);sm=abs(pct(spotcl[-4],spotcl[-1]));ev.append(E(d,x.timestamp,st,cur,m1,m3,vr,comp,sm,(d,st)))
 ev.sort(key=lambda z:z.ts);return ev,combo_map,time_map,sorted(sbd)
def q(e,c):return e.move1>=c.min_move1 and e.move3>=c.min_move3 and e.volratio>=c.min_vol and e.compression<=c.max_compression and e.spotmove>=c.min_spotmove
def sim(e,c,cm,tm):
 s=cm.get(e.key);ts=tm.get(e.key)
 if not s or not ts:return None
 i=bisect_right(ts,e.ts)
 if i>=len(s) or s[i][0].date()!=e.day:return None
 entry=s[i][1]+.40;tp=entry+c.target;sl=entry-c.stop;et=s[i][0];cut=et+timedelta(minutes=c.hold);last=entry
 for z in s[i:]:
  if z[0].date()!=e.day or z[0]>cut:break
  last=z[2]
  if last<=sl:return T(e.day,e.ts,et,e.strike,'LOSS',-c.stop-1.)
  if last>=tp:return T(e.day,e.ts,et,e.strike,'WIN',c.target-1.)
 return T(e.day,e.ts,et,e.strike,'TIMEOUT',last-entry-1.)
def stats(t,days):
 n=len(t);w=sum(x.outcome=='WIN' for x in t);net=sum(x.net for x in t);g=sum(max(0,x.net) for x in t);lv=abs(sum(min(0,x.net) for x in t));pf=g/lv if lv else(g if g else 0.);dc=defaultdict(int);dn=defaultdict(float);eq=pk=dd=0.
 for x in sorted(t,key=lambda z:z.entry_time):dc[x.day]+=1;dn[x.day]+=x.net;eq+=x.net;pk=max(pk,eq);dd=max(dd,pk-eq)
 nd=len(days);return {'trades':n,'wins':w,'losses':n-w,'win_rate':round(100*w/n,2) if n else 0.,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.,'profit_factor':round(pf,2),'max_drawdown':round(dd,2),'trading_days':nd,'avg_trades_per_day':round(n/nd,2) if nd else 0.,'avg_net_points_per_day':round(net/nd,2) if nd else 0.,'pct_days_with_3plus_trades':round(100*sum(dc[d]>=3 for d in days)/nd,2) if nd else 0.,'pct_profitable_days':round(100*sum(dn[d]>0 for d in days)/nd,2) if nd else 0.}
def eval(ev,c,cm,tm,days,a,b):
 out=[];last=defaultdict(lambda:None);cnt=defaultdict(int)
 for e in ev:
  if not(a<=e.day<=b) or not q(e,c) or cnt[e.day]>=5:continue
  if last[e.day] is not None and (e.ts-last[e.day]).total_seconds()<c.cooldown*60:continue
  t=sim(e,c,cm,tm)
  if t is not None:out.append(t);last[e.day]=t.entry_time;cnt[e.day]+=1
 ds=[d for d in days if a<=d<=b];return stats(out,ds),tuple(out)
def grid():
 for v in itertools.product((0.,.15,.3),(0.,.25,.5),(.8,1.1,1.4),(1.,2.,3.),(0.,.03,.06),((8.,4.),(10.,5.),(12.,6.)),(15,25),(5,8)):
  yield C(v[0],v[1],v[2],v[3],v[4],v[5][0],v[5][1],v[6],v[7])
def score(s):
 if s['trades']<250 or s['avg_trades_per_day']<2.5 or s['expectancy']<=0 or s['profit_factor']<=1:return -1e9
 return s['win_rate']*1.5+s['avg_net_points_per_day']*1.6+min(s['profit_factor'],4)*5+s['pct_profitable_days']*.15-s['max_drawdown']/120

def main():
 ev,cm,tm,days=build();train=(date(2025,7,1),date(2025,12,31));val=(date(2026,1,1),date(2026,3,31));stress=(date(2026,4,1),date(2026,6,30));good=[];fall=[]
 for c in grid():
  s,_=eval(ev,c,cm,tm,days,*train);sc=score(s);fall.append((s['win_rate']+max(s['expectancy'],0)*2+s['avg_trades_per_day'],c,s));
  if sc>-1e8:good.append((sc,c,s))
 if good:good.sort(key=lambda z:z[0],reverse=True);_,c,tr=good[0]
 else:fall.sort(key=lambda z:z[0],reverse=True);_,c,tr=fall[0]
 vs,vt=eval(ev,c,cm,tm,days,*val);ss,st=eval(ev,c,cm,tm,days,*stress);vp=vs['trades']>=150 and 3<=vs['avg_trades_per_day']<=5 and vs['win_rate']>=70 and vs['expectancy']>=3.5 and vs['profit_factor']>=2 and vs['avg_net_points_per_day']>=15 and vs['pct_profitable_days']>=70;sp=ss['trades']>=125 and ss['avg_trades_per_day']>=2.5 and ss['win_rate']>=65 and ss['expectancy']>0 and ss['profit_factor']>=1.5 and ss['avg_net_points_per_day']>=10
 out={'search_name':'Shiv ATM Long-Straddle Expansion Scalp','candidate_count':len(list(grid())),'events_built':len(ev),'status':'PROVEN_70_PLUS_DAILY' if vp and sp else 'NO_70_PLUS_DAILY_PROOF','candidate':asdict(c),'training':tr,'validation':vs,'stress':ss,'validation_pass':vp,'stress_pass':sp,'proof_rule':'Jan-Mar >=150 trades, 3-5/day, >=70% wins, expectancy>=3.5 combined premium points/trade, PF>=2, >=15 net combined option points/day; Apr-Jun stress >=65% and positive economics.','execution':'Buy ATM CE+PE together at next-minute opens after combined-premium expansion from compression; combined close-to-close exits only (conservative intraminute treatment), 0.40 entry slippage +1.00 total friction, max 5/day.'};Path('strategy_70_straddle_scalp.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
