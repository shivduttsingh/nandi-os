from __future__ import annotations
import itertools,json,sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import search_strategy_70_multisetup as m
from test1.public_backtest import _download_public_sample,_parse_option_frame,_parse_spot_frame,_row_to_candle

def regime_map(sbd,eff_th,range_th):
 out={}
 for d,cs in sbd.items():
  x=[c for c in cs if time(9,15)<=c.timestamp.time()<=time(9,45)]
  if len(x)<15:continue
  o=x[0].open;cl=x[-1].close;hi=max(c.high for c in x);lo=min(c.low for c in x);span=max(hi-lo,1e-9);eff=abs(cl-o)/span;rp=100*span/max(o,1e-9);out[d]='TREND' if eff>=eff_th and rp>=range_th else 'CHOP'
 return out

def configs():
 trend=[];chop=[]
 for il,pl,score,pair,cd in itertools.product((0,1),(0,1),(.45,.55),((8.,4.),(10.,5.)),(5,8)):
  trend.append(m.Config(il,pl,1,score,pair[0],pair[1],cd,3))
 for rl,score,pair,cd in itertools.product((0,1),(.45,.55),((8.,4.),(10.,5.)),(5,8)):
  chop.append(m.Config(1,1,rl,score,pair[0],pair[1],cd,3))
 return trend,chop

def stats(trades,days,a,b):return m.generic_stats(trades),m.daily_stats(trades,days,a,b)
def combine(trend_trades,chop_trades,rmap,a,b):
 out=[]
 for t in trend_trades:
  if a<=t.day<=b and rmap.get(t.day)=='TREND':out.append(t)
 for t in chop_trades:
  if a<=t.day<=b and rmap.get(t.day)=='CHOP':out.append(t)
 out.sort(key=lambda z:z.entry_time);return out

def score(st,ds):
 if st['trades']<max(150,int(ds['trading_days']*2.4)) or ds['avg_trades_per_day']<2.4 or st['expectancy']<=0 or st['profit_factor']<=1:return -1e9
 return st['win_rate']*1.5+ds['avg_net_points_per_day']*1.7+min(st['profit_factor'],4)*5+ds['pct_profitable_days']*.15-st['max_drawdown']/150

def main():
 p=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'));sp=_parse_spot_frame(p);op=_parse_option_frame(p);lo,hi=date(2025,7,1),date(2026,6,30);sp=sp[(sp.timestamp.dt.date>=lo)&(sp.timestamp.dt.date<=hi)];op=op[(op.day>=lo)&(op.day<=hi)];sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in sp.groupby(sp.timestamp.dt.date,sort=True)};rows=defaultdict(list)
 for r in op.itertuples(index=False):rows[r.day].append(r)
 ie,ism,itm=m.imp.build_events(sbd,rows);pe,psm,ptm=m.pull.build_events(sbd,rows);re,rsm,rtm=m.rev.build_events(sbd,rows);events={'IMPULSE':ie,'PULLBACK':pe,'REVERSAL':re};maps={'IMPULSE':(ism,itm),'PULLBACK':(psm,ptm),'REVERSAL':(rsm,rtm)};days=sorted(sbd);trend_cfgs,chop_cfgs=configs();full=(date(2025,7,1),date(2026,6,30));cache={}
 for c in trend_cfgs+chop_cfgs:
  key=tuple(asdict(c).values())
  if key not in cache:cache[key]=m.evaluate(c,events,maps,days,*full)[2]
 train=(date(2025,7,1),date(2025,12,31));val=(date(2026,1,1),date(2026,3,31));stress=(date(2026,4,1),date(2026,6,30));rank=[];fallback=[]
 for eff,rg,tc,cc in itertools.product((.4,.55,.7),(.15,.25,.35),trend_cfgs,chop_cfgs):
  rm=regime_map(sbd,eff,rg);tr=combine(cache[tuple(asdict(tc).values())],cache[tuple(asdict(cc).values())],rm,*train);st,ds=stats(tr,days,*train);sc=score(st,ds);row=(sc,eff,rg,tc,cc,st,ds);fallback.append((st['win_rate']+max(st['expectancy'],0)*2+ds['avg_trades_per_day'],row));
  if sc>-1e8:rank.append(row)
 if rank:rank.sort(key=lambda z:z[0],reverse=True);row=rank[0]
 else:fallback.sort(key=lambda z:z[0],reverse=True);row=fallback[0][1]
 _,eff,rg,tc,cc,trst,trds=row;rm=regime_map(sbd,eff,rg);vt=combine(cache[tuple(asdict(tc).values())],cache[tuple(asdict(cc).values())],rm,*val);stt=combine(cache[tuple(asdict(tc).values())],cache[tuple(asdict(cc).values())],rm,*stress);vs,vds=stats(vt,days,*val);ss,sds=stats(stt,days,*stress);vp=vs['trades']>=150 and 3<=vds['avg_trades_per_day']<=5 and vs['win_rate']>=70 and vs['expectancy']>=3.5 and vs['profit_factor']>=2 and vds['avg_net_points_per_day']>=15 and vds['pct_profitable_days']>=70;spass=ss['trades']>=125 and sds['avg_trades_per_day']>=2.5 and ss['win_rate']>=65 and ss['expectancy']>0 and ss['profit_factor']>=1.5 and sds['avg_net_points_per_day']>=10
 out={'search_name':'Shiv Opening-Regime Multi-Setup Selector','status':'PROVEN_70_PLUS_DAILY' if vp and spass else 'NO_70_PLUS_DAILY_PROOF','regime':{'efficiency_threshold':eff,'opening_range_pct_threshold':rg},'trend_config':asdict(tc),'chop_config':asdict(cc),'training':{'stats':trst,'daily':trds},'validation':{'stats':vs,'daily':vds},'stress':{'stats':ss,'daily':sds},'validation_pass':vp,'stress_pass':spass,'proof_rule':'Opening 09:15-09:45 classifies day before later entries. Jan-Mar >=150 trades, 3-5/day, >=70% wins, expectancy>=3.5, PF>=2, >=15 net option points/day, >=70% profitable days; Apr-Jun stress >=65% and positive economics.'};Path('strategy_70_regime_selector.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
