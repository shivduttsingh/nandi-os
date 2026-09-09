from __future__ import annotations

import json, sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date, time, timedelta
from pathlib import Path
from statistics import median, mean
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from test1.public_backtest import _download_public_sample,_nearest_common_strike,_parse_option_frame,_parse_spot_frame,_row_to_candle

FEATURES=['side_sign','spot_r1','spot_r3','spot_r5','spot_r10','spot_range_atr','spot_body_atr','spot_pos20','spot_vol_ratio','opt_r1','opt_r3','opt_r5','opp_r1','opp_r3','opp_r5','rel_r1','rel_r3','rel_r5','opt_vol_ratio','opp_vol_ratio','opt_oi3','opt_oi5','opp_oi3','opp_oi5','premium','premium_range_pct','premium_vs_med10','minute_frac']
GEOMETRIES=((8.,4.),(10.,5.),(6.,3.))
MODEL_SPECS=(
 {'max_leaf_nodes':15,'learning_rate':.05,'max_iter':120,'min_samples_leaf':20,'l2_regularization':1.},
 {'max_leaf_nodes':31,'learning_rate':.04,'max_iter':150,'min_samples_leaf':20,'l2_regularization':1.5},
 {'max_leaf_nodes':15,'learning_rate':.08,'max_iter':100,'min_samples_leaf':35,'l2_regularization':2.},
)
THRESHOLDS=(.52,.56,.60,.64,.68,.72,.76,.80)
COOLDOWNS=(4,6,8,10)

def pct(a,b): return ((b/a)-1)*100 if a and a>0 else 0.
def atr(c,lb=14):
 if len(c)<2:return 0.
 s=c[-min(lb,len(c)-1):];p=c[-len(s)-1].close if len(c)>len(s) else c[0].close;v=[]
 for x in s:v.append(max(x.high-x.low,abs(x.high-p),abs(x.low-p)));p=x.close
 return mean(v) if v else 0.
def vr(c,lb=12):
 h=[x.volume for x in c[-lb-1:-1] if x.volume and x.volume>0]
 if not h:return 0.
 b=median(h);return c[-1].volume/b if b>0 else 0.
def ret(c,n):return pct(c[-n-1].close,c[-1].close) if len(c)>=n+1 else 0.
def oi(c,n):
 if len(c)<n+1:return 0.
 o=c[-n-1].open_interest;return pct(o,c[-1].open_interest) if o and o>0 else 0.

def sim(series,times,ts,target,stop,hold=20):
 i=bisect_right(times,ts)
 if i>=len(series) or series[i].timestamp.date()!=ts.date():return None
 entry=series[i].open+.20;sl=max(.05,entry-stop);tp=entry+target;cut=series[i].timestamp+timedelta(minutes=hold);f=[]
 for x in series[i:]:
  if x.timestamp.date()!=ts.date() or x.timestamp>cut:break
  f.append(x)
 if not f:return None
 for x in f:
  if x.low<=sl:return 0,-stop-.50,'LOSS'
  if x.high>=tp:return 1,target-.50,'WIN'
 return 0,f[-1].close-entry-.50,'TIMEOUT'

def build():
 p=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(p);op=_parse_option_frame(p);lo,hi=date(2025,7,1),date(2026,6,30)
 sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)]
 sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};ors=defaultdict(list)
 for r in op.itertuples(index=False):ors[r.day].append(r)
 rows=[];smap={};tmap={}
 for d in sorted(sbd):
  ds=sbd[d];raw=ors.get(d,[])
  if len(ds)<100 or not raw:continue
  at=defaultdict(list);lists=defaultdict(list);strikes={'CE':set(),'PE':set()}
  for r in raw:
   side='CE' if r.option_type in {'CE','CALL'} else 'PE' if r.option_type in {'PE','PUT'} else ''
   if not side:continue
   st=int(r.strike);c=_row_to_candle(r);at[r.timestamp].append((side,st,c));lists[(side,st)].append(c);strikes[side].add(st)
  if not(strikes['CE']&strikes['PE']):continue
  for k,v in lists.items():
   key=(d,k[0],k[1]);seq=tuple(sorted(v,key=lambda x:x.timestamp));smap[key]=seq;tmap[key]=[x.timestamp for x in seq]
  hist=defaultdict(list);sh=[]
  for c in ds:
   sh.append(c)
   for side,st,oc in at.get(c.timestamp,[]):hist[(side,st)].append(oc)
   if not(time(9,35)<=c.timestamp.time()<=time(14,20)) or len(sh)<25:continue
   mo=(c.timestamp.hour*60+c.timestamp.minute)-(9*60+15)
   if mo%2:continue
   a=atr(sh[-25:]);st=_nearest_common_strike(strikes,c.close)
   if a<=0 or st is None:continue
   low=min(x.low for x in sh[-20:]);high=max(x.high for x in sh[-20:]);span=max(high-low,1e-9)
   base={'spot_r1':ret(sh,1),'spot_r3':ret(sh,3),'spot_r5':ret(sh,5),'spot_r10':ret(sh,10),'spot_range_atr':(c.high-c.low)/a,'spot_body_atr':abs(c.close-c.open)/a,'spot_pos20':(c.close-low)/span,'spot_vol_ratio':vr(sh),'minute_frac':mo/375.}
   for side in ('CE','PE'):
    opp='PE' if side=='CE' else 'CE';ch=hist.get((side,st),[]);oh=hist.get((opp,st),[])
    if len(ch)<14 or len(oh)<14:continue
    last=ch[-1];med=median([x.close for x in ch[-10:]]);pr=max(last.high-last.low,1e-9)
    rows.append({'day':d,'timestamp':c.timestamp,'side':side,'strike':st,'series_key':(d,side,st),'side_sign':1. if side=='CE' else -1.,**base,
     'opt_r1':ret(ch,1),'opt_r3':ret(ch,3),'opt_r5':ret(ch,5),'opp_r1':ret(oh,1),'opp_r3':ret(oh,3),'opp_r5':ret(oh,5),'rel_r1':ret(ch,1)-ret(oh,1),'rel_r3':ret(ch,3)-ret(oh,3),'rel_r5':ret(ch,5)-ret(oh,5),'opt_vol_ratio':vr(ch),'opp_vol_ratio':vr(oh),'opt_oi3':oi(ch,3),'opt_oi5':oi(ch,5),'opp_oi3':oi(oh,3),'opp_oi5':oi(oh,5),'premium':last.close,'premium_range_pct':pr/max(last.close,.05)*100.,'premium_vs_med10':pct(med,last.close) if med>0 else 0.})
 return pd.DataFrame(rows),smap,tmap,sorted(sbd)

def label(df,smap,tmap,target,stop):
 ys=[];ns=[];os=[]
 for r in df.itertuples(index=False):
  z=sim(smap.get(r.series_key,()),tmap.get(r.series_key,()),r.timestamp,target,stop)
  if z is None:ys.append(np.nan);ns.append(np.nan);os.append('NONE')
  else:ys.append(z[0]);ns.append(z[1]);os.append(z[2])
 x=df.copy();x['label']=ys;x['net_points']=ns;x['outcome']=os;x=x.dropna(subset=['label','net_points']).copy();x['label']=x.label.astype(int);return x

def fit(df,spec):
 X=df[FEATURES].replace([np.inf,-np.inf],0).fillna(0).to_numpy(float);y=df.label.to_numpy(int);p=max(1,int(y.sum()));n=max(1,len(y)-p);w=np.where(y==1,len(y)/(2*p),len(y)/(2*n));m=HistGradientBoostingClassifier(random_state=42,**spec);m.fit(X,y,sample_weight=w);return m
def pred(m,df):
 x=df.copy();X=x[FEATURES].replace([np.inf,-np.inf],0).fillna(0).to_numpy(float);x['prob']=m.predict_proba(X)[:,1];return x

def select(df,th,cd,days):
 by=defaultdict(list)
 for r in df.itertuples(index=False):by[r.day].append(r)
 out=[]
 for d in days:
  arr=sorted(by.get(d,[]),key=lambda r:(r.timestamp,-r.prob));last=None;used=set();cnt=0;best_at={}
  for r in arr:
   if r.prob>=th and (r.timestamp not in best_at or r.prob>best_at[r.timestamp].prob):best_at[r.timestamp]=r
  for ts in sorted(best_at):
   if cnt>=5:break
   r=best_at[ts]
   if last is not None and (ts-last).total_seconds()<cd*60:continue
   out.append(r);last=ts;cnt+=1
 return out

def metrics(t,days):
 n=len(t);w=sum(int(x.label)==1 for x in t);net=sum(float(x.net_points) for x in t);g=sum(max(0,float(x.net_points)) for x in t);lv=abs(sum(min(0,float(x.net_points)) for x in t));pf=g/lv if lv else(g if g else 0);dc=defaultdict(int);dn=defaultdict(float);eq=pk=dd=0.
 for x in sorted(t,key=lambda r:r.timestamp):
  dc[x.day]+=1;dn[x.day]+=float(x.net_points);eq+=float(x.net_points);pk=max(pk,eq);dd=max(dd,pk-eq)
 nd=len(days)
 return {'trades':n,'wins':w,'losses':n-w,'win_rate':round(100*w/n,2) if n else 0.,'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.,'profit_factor':round(pf,2),'max_drawdown':round(dd,2),'trading_days':nd,'avg_trades_per_day':round(n/nd,2) if nd else 0.,'avg_net_points_per_day':round(net/nd,2) if nd else 0.,'pct_days_with_3plus_trades':round(100*sum(dc[d]>=3 for d in days)/nd,2) if nd else 0.,'pct_profitable_days':round(100*sum(dn[d]>0 for d in days)/nd,2) if nd else 0.,'pct_days_15plus_points':round(100*sum(dn[d]>=15 for d in days)/nd,2) if nd else 0.}
def pdays(days,a,b):return [d for d in days if a<=d<=b]
def score(m):
 if m['trades']<max(60,int(m['trading_days']*2)) or m['avg_trades_per_day']<2.3 or m['profit_factor']<=1 or m['expectancy']<=0:return -1e9
 return m['win_rate']*1.5+m['avg_net_points_per_day']*1.8+min(m['profit_factor'],4)*5+m['pct_profitable_days']*.15-m['max_drawdown']/150

def main():
 base,smap,tmap,days=build();trainw=(date(2025,7,1),date(2025,10,31));calw=(date(2025,11,1),date(2025,12,31));valw=(date(2026,1,1),date(2026,3,31));stressw=(date(2026,4,1),date(2026,6,30));recs=[];best=None
 for tp,sl in GEOMETRIES:
  lab=label(base,smap,tmap,tp,sl);tr=lab[(lab.day>=trainw[0])&(lab.day<=trainw[1])];ca=lab[(lab.day>=calw[0])&(lab.day<=calw[1])];cdays=pdays(days,*calw)
  for mi,spec in enumerate(MODEL_SPECS):
   mod=fit(tr,spec);cp=pred(mod,ca)
   for th in THRESHOLDS:
    for cd in COOLDOWNS:
     mm=metrics(select(cp,th,cd,cdays),cdays);sc=score(mm);r={'target':tp,'stop':sl,'model_index':mi,'model_spec':spec,'threshold':th,'cooldown':cd,'calibration':mm,'score':round(sc,4)};recs.append(r)
     if best is None or sc>best['score']:best=r
 if best is None or best['score']<=-1e8:
  best=sorted(recs,key=lambda r:(r['calibration']['win_rate'],r['calibration']['avg_net_points_per_day'],r['calibration']['avg_trades_per_day']),reverse=True)[0] if recs else None
 out={'search_name':'Shiv ML CE-PE Daily Selector 70 Proof','anti_leakage':'Train Jul-Oct 2025; choose geometry/model/threshold/cooldown on Nov-Dec only; freeze before Jan 2026; validate Jan-Mar; stress Apr-Jun without changes.','sample_count':int(len(base)),'feature_count':len(FEATURES),'best_calibration_candidate':best}
 if best is None:out['status']='NO_CANDIDATE';Path('strategy_70_ml_selector.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2));return
 tp,sl=best['target'],best['stop'];lab=label(base,smap,tmap,tp,sl);final=lab[(lab.day>=date(2025,7,1))&(lab.day<=date(2025,12,31))];mod=fit(final,best['model_spec']);vd=pdays(days,*valw);sd=pdays(days,*stressw);v=lab[(lab.day>=valw[0])&(lab.day<=valw[1])];s=lab[(lab.day>=stressw[0])&(lab.day<=stressw[1])];vt=select(pred(mod,v),best['threshold'],best['cooldown'],vd);st=select(pred(mod,s),best['threshold'],best['cooldown'],sd);vm=metrics(vt,vd);sm=metrics(st,sd)
 vp=vm['trading_days']>=50 and vm['trades']>=150 and 3<=vm['avg_trades_per_day']<=5 and vm['pct_days_with_3plus_trades']>=65 and vm['win_rate']>=70 and vm['expectancy']>=3.5 and vm['profit_factor']>=2 and vm['avg_net_points_per_day']>=15 and vm['pct_profitable_days']>=70
 sp=sm['trading_days']>=50 and sm['trades']>=125 and sm['avg_trades_per_day']>=2.5 and sm['win_rate']>=65 and sm['expectancy']>0 and sm['profit_factor']>=1.5 and sm['avg_net_points_per_day']>=10
 out.update({'status':'PROVEN_70_PLUS_DAILY' if vp and sp else 'NO_70_PLUS_DAILY_PROOF','proof_rule':'Jan-Mar >=150 trades, 3-5/day, >=70% target wins, expectancy >=3.5, PF>=2, >=15 net option points/day, >=70% profitable days; Apr-Jun stress >=65% wins and positive economics.','execution':'Completed-minute features only; next ATM option minute open +0.20 slippage; same-candle stop first; 0.50 friction; max 5/day.','validation':vm,'stress':sm,'validation_pass':vp,'stress_pass':sp,'validation_trades':[{'day':x.day.isoformat(),'timestamp':x.timestamp.isoformat(),'side':x.side,'strike':int(x.strike),'prob':round(float(x.prob),4),'outcome':x.outcome,'net_points':round(float(x.net_points),2)} for x in vt],'stress_trades':[{'day':x.day.isoformat(),'timestamp':x.timestamp.isoformat(),'side':x.side,'strike':int(x.strike),'prob':round(float(x.prob),4),'outcome':x.outcome,'net_points':round(float(x.net_points),2)} for x in st]});Path('strategy_70_ml_selector.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
