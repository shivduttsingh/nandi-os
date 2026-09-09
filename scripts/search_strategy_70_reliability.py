from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import search_strategy_70_multisetup as base
from scripts import search_strategy_70_exhaustion_reversal as rev
from scripts import search_strategy_70_option_impulse as imp
from scripts import search_strategy_70_trend as pull
from test1.public_backtest import _download_public_sample, _parse_option_frame, _parse_spot_frame, _row_to_candle


@dataclass(frozen=True)
class Config:
    target_points: float
    stop_points: float
    time_bucket_minutes: int
    min_bucket_trades: int
    min_bucket_win_rate: float
    min_bucket_pf: float
    cooldown_minutes: int
    max_trades_day: int = 5


@dataclass(frozen=True)
class Sig:
    family: str
    event: object
    score: float


@dataclass(frozen=True)
class Trade:
    day: date
    family: str
    signal_time: object
    entry_time: object
    direction: str
    strike: int
    outcome: str
    net_points: float


def broad_score(family, event):
    if family == 'IMPULSE':
        return base.impulse_pass_score(event, 0)
    if family == 'PULLBACK':
        return base.pullback_pass_score(event, 0)
    return base.reversal_pass_score(event, 0)


def score_tier(score: float):
    if score < 0.50:
        return 'S0'
    if score < 0.60:
        return 'S1'
    if score < 0.70:
        return 'S2'
    return 'S3'


def bucket_key(sig: Sig, minutes: int):
    t = sig.event.signal_time
    minute_of_day = t.hour * 60 + t.minute
    block = minute_of_day // minutes
    return (sig.family, sig.event.direction, block, score_tier(sig.score))


def sim(sig: Sig, cfg: Config, maps, cache):
    e = sig.event
    key = (sig.family, e.day, e.signal_time, e.direction, e.strike, cfg.target_points, cfg.stop_points)
    if key in cache:
        return cache[key]
    wrapped = base.Wrapped(sig.family, e, sig.score)
    synthetic = base.Config(0,0,0,0.0,cfg.target_points,cfg.stop_points,cfg.cooldown_minutes,5,cfg.max_trades_day)
    tr = base.simulate_wrapped(wrapped, synthetic, maps)
    if tr is None:
        cache[key] = None
        return None
    out = Trade(tr.day,tr.family,tr.signal_time,tr.entry_time,tr.direction,tr.strike,tr.outcome,tr.net_points)
    cache[key] = out
    return out


def trade_stats(trades):
    t = tuple(trades); n = len(t)
    w = sum(x.outcome == 'WIN' for x in t)
    l = sum(x.outcome == 'LOSS' for x in t)
    to = n - w - l
    net = sum(x.net_points for x in t)
    gain = sum(max(0.0,x.net_points) for x in t)
    loss = abs(sum(min(0.0,x.net_points) for x in t))
    pf = gain/loss if loss else (gain if gain else 0.0)
    eq = peak = dd = 0.0
    for x in t:
        eq += x.net_points; peak = max(peak,eq); dd = max(dd,peak-eq)
    return {
        'trades':n,'wins':w,'losses':l,'timeouts':to,
        'win_rate':round(100*w/n,2) if n else 0.0,
        'net_points':round(net,2),'expectancy':round(net/n,2) if n else 0.0,
        'profit_factor':round(pf,2),'max_drawdown':round(dd,2),
    }


def daily_stats(trades, days, start, end):
    active = [d for d in days if start <= d <= end]
    c = defaultdict(int); p = defaultdict(float); fam = defaultdict(int)
    for t in trades:
        c[t.day] += 1; p[t.day] += t.net_points; fam[t.family] += 1
    n = len(active)
    return {
        'trading_days':n,
        'avg_trades_per_day':round(len(trades)/n,2) if n else 0.0,
        'avg_net_points_per_day':round(sum(p.values())/n,2) if n else 0.0,
        'pct_days_with_3plus_trades':round(100*sum(c[d]>=3 for d in active)/n,2) if n else 0.0,
        'pct_profitable_days':round(100*sum(p[d]>0 for d in active)/n,2) if n else 0.0,
        'pct_days_15plus_points':round(100*sum(p[d]>=15 for d in active)/n,2) if n else 0.0,
        'family_totals':dict(fam),
    }


def build_whitelist(cfg, signals, maps, cache, train_start, train_end):
    outcomes = defaultdict(list)
    for s in signals:
        if not (train_start <= s.event.day <= train_end):
            continue
        tr = sim(s,cfg,maps,cache)
        if tr is None:
            continue
        outcomes[bucket_key(s,cfg.time_bucket_minutes)].append(tr)
    whitelist = set(); detail = {}
    for k, trades in outcomes.items():
        st = trade_stats(trades)
        detail['|'.join(map(str,k))] = st
        if (
            st['trades'] >= cfg.min_bucket_trades
            and st['win_rate'] >= cfg.min_bucket_win_rate
            and st['expectancy'] > 0
            and st['profit_factor'] >= cfg.min_bucket_pf
        ):
            whitelist.add(k)
    return whitelist, detail


def evaluate(cfg, signals, maps, cache, whitelist, days, start, end):
    by = defaultdict(list)
    for s in signals:
        if start <= s.event.day <= end and bucket_key(s,cfg.time_bucket_minutes) in whitelist:
            by[s.event.day].append(s)
    out = []
    for d in [x for x in days if start <= x <= end]:
        recent_entry = None; seen = set()
        for s in sorted(by.get(d,[]),key=lambda x:x.event.signal_time):
            if len([t for t in out if t.day == d]) >= cfg.max_trades_day:
                break
            if recent_entry is not None and (s.event.signal_time-recent_entry).total_seconds() < cfg.cooldown_minutes*60:
                continue
            ek = (s.event.signal_time,s.event.direction,s.event.strike)
            if ek in seen:
                continue
            tr = sim(s,cfg,maps,cache)
            if tr is None:
                continue
            out.append(tr); recent_entry = tr.entry_time; seen.add(ek)
    return trade_stats(out), daily_stats(out,days,start,end), tuple(out)


def grid():
    for pair,bucket,ntr,wr,pf,cd in itertools.product(
        ((8.0,4.0),(10.0,5.0)),
        (30,60),
        (15,25),
        (55.0,60.0,65.0),
        (1.10,1.30),
        (5,8),
    ):
        yield Config(pair[0],pair[1],bucket,ntr,wr,pf,cd)


def rank(st,ds):
    n=st['trades']
    if n < max(80,ds['trading_days']*1.5) or ds['avg_trades_per_day'] < 1.5:
        return -1e9
    if st['expectancy'] <= 0 or st['profit_factor'] <= 1:
        return -1e9
    p=st['wins']/n; z=1.0
    lb=(p+z*z/(2*n)-z*math.sqrt((p*(1-p)+z*z/(4*n))/n))/(1+z*z/n)
    return lb*100 + min(st['profit_factor'],4)*5 + min(st['expectancy'],8)*2 + min(ds['avg_net_points_per_day'],25) + ds['pct_profitable_days']/10


def period(cfg,signals,maps,cache,whitelist,days,start,end):
    st,ds,tr=evaluate(cfg,signals,maps,cache,whitelist,days,start,end)
    return {'stats':st,'daily':ds,'trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in tr]}


def exact(p):
    st,ds=p['stats'],p['daily']
    return ds['trading_days']>=55 and 3<=ds['avg_trades_per_day']<=5 and ds['pct_days_with_3plus_trades']>=65 and st['win_rate']>=70 and st['expectancy']>=3.5 and st['profit_factor']>=2 and ds['avg_net_points_per_day']>=15 and ds['pct_profitable_days']>=70


def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot=_parse_spot_frame(path); opt=_parse_option_frame(path); lo,hi=date(2025,7,1),date(2026,6,30)
    spot=spot[(spot.timestamp.dt.date>=lo)&(spot.timestamp.dt.date<=hi)]; opt=opt[(opt.day>=lo)&(opt.day<=hi)]
    sbd={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot.groupby(spot.timestamp.dt.date,sort=True)}
    rows=defaultdict(list)
    for r in opt.itertuples(index=False):rows[r.day].append(r)
    ie,ism,itm=imp.build_events(sbd,rows); pe,psm,ptm=pull.build_events(sbd,rows); re,rsm,rtm=rev.build_events(sbd,rows)
    streams={'IMPULSE':ie,'PULLBACK':pe,'REVERSAL':re}; maps={'IMPULSE':(ism,itm),'PULLBACK':(psm,ptm),'REVERSAL':(rsm,rtm)}
    signals=[]
    for fam,evs in streams.items():
        for e in evs:
            s=broad_score(fam,e)
            if s is not None:signals.append(Sig(fam,e,s))
    signals.sort(key=lambda x:x.event.signal_time); days=sorted(sbd); cache={}
    train=(date(2025,7,1),date(2025,12,31)); valw=(date(2026,1,1),date(2026,3,31)); stressw=(date(2026,4,1),date(2026,6,30))
    ranked=[]; near=[]; configs=list(grid())
    for cfg in configs:
        wl,detail=build_whitelist(cfg,signals,maps,cache,*train)
        st,ds,_=evaluate(cfg,signals,maps,cache,wl,days,*train)
        raw=st['win_rate']+st['profit_factor']*3+st['expectancy']*2+ds['avg_trades_per_day']*4+ds['avg_net_points_per_day']+ds['pct_profitable_days']/10
        near.append((raw,cfg,st,ds,wl,detail)); sc=rank(st,ds)
        if sc>-1e8:ranked.append((sc,cfg,st,ds,wl,detail))
    near.sort(key=lambda x:x[0],reverse=True); ranked.sort(key=lambda x:x[0],reverse=True)
    best=None
    if near:
        _,c,st,ds,wl,_=near[0];best={'config':asdict(c),'stats':st,'daily':ds,'whitelist_bucket_count':len(wl)}
    if not ranked:
        payload={'search_name':'Shiv Frozen Reliability-Bucket Daily Proof','status':'NO_TRAINING_CANDIDATE','config_count':len(configs),'broad_signal_count':len(signals),'events_built':{k:len(v) for k,v in streams.items()},'method':'Training-only whitelist by family + direction + time bucket + signal-quality tier. Whitelist frozen before 2026. Sequential live-style max 5/day; no future/day-end ranking.','best_available_training':best}
    else:
        _,cfg,trst,trds,wl,detail=ranked[0]
        val=period(cfg,signals,maps,cache,wl,days,*valw); stress=period(cfg,signals,maps,cache,wl,days,*stressw); comb=period(cfg,signals,maps,cache,wl,days,valw[0],stressw[1]);proven=exact(val) and exact(stress) and comb['stats']['win_rate']>=70 and comb['daily']['avg_net_points_per_day']>=15
        payload={'search_name':'Shiv Frozen Reliability-Bucket Daily Proof','status':'PROVEN_EXACT_TARGET' if proven else 'NO_EXACT_TARGET_PROVEN','config_count':len(configs),'broad_signal_count':len(signals),'events_built':{k:len(v) for k,v in streams.items()},'method':'Training-only whitelist by family + direction + time bucket + signal-quality tier. Whitelist frozen before 2026. Sequential live-style max 5/day; no future/day-end ranking.','proof_rule':'Jan-Mar AND Apr-Jun each: 3-5 trades/day, >=70% wins, PF>=2, expectancy>=3.5, >=15 net option pts/day, >=70% profitable days.','chosen_config':asdict(cfg),'whitelist_bucket_count':len(wl),'training':{'stats':trst,'daily':trds},'validation':val,'stress':stress,'combined_oos':comb,'best_available_training':best}
    Path('strategy_70_reliability.json').write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))


if __name__=='__main__':main()
