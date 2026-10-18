from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample, _nearest_common_strike, _parse_option_frame, _parse_spot_frame, _row_to_candle, _update_aggregate


@dataclass(frozen=True)
class Event:
    day: date
    signal_time: datetime
    direction: str
    strike: int
    spot_trend_gap_atr: float
    spot_move_atr: float
    option_move_pct: float
    option_outperformance_pct: float
    option_volume_ratio: float
    option_ema_aligned: bool
    option_premium: float
    option_oi_change_pct: float
    option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    min_spot_gap_atr: float
    min_spot_move_atr: float
    min_option_move_pct: float
    min_outperformance_pct: float
    min_volume_ratio: float
    require_option_ema: bool
    min_premium: float
    target_points: float
    stop_points: float


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: datetime
    entry_time: datetime
    direction: str
    strike: int
    outcome: str
    net_points: float


@dataclass(frozen=True)
class Stats:
    trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float
    net_points: float
    expectancy: float
    profit_factor: float
    max_drawdown: float


def pct(a: float, b: float) -> float:
    return ((b / a) - 1.0) * 100.0 if a > 0 else 0.0


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def atr(candles: list[IntradayCandle] | tuple[IntradayCandle, ...], lookback: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    previous = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    values = []
    for candle in sample:
        values.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return mean(values) if values else 0.0


def move_pct(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def volume_ratio(candles: list[IntradayCandle], lookback: int = 12) -> float:
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def oi_change(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    return pct(old, candles[-1].open_interest) if old > 0 else 0.0


def build_events(spot_by_day, option_rows_by_day):
    events: list[Event] = []
    series_map = {}
    times_map = {}
    for day_value in sorted(spot_by_day):
        day_spot = spot_by_day[day_value]
        raw_options = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 100 or not raw_options:
            continue
        options_at_time = defaultdict(list)
        option_lists = defaultdict(list)
        strikes_by_side = {"CE": set(), "PE": set()}
        for row in raw_options:
            side = "CE" if row.option_type in {"CE","CALL"} else "PE" if row.option_type in {"PE","PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side,row))
            option_lists[(side,strike)].append(candle)
            strikes_by_side[side].add(strike)
        if not (strikes_by_side['CE'] & strikes_by_side['PE']):
            continue
        for key, values in option_lists.items():
            sk=(day_value,key[0],key[1])
            series=tuple(sorted(values,key=lambda c:c.timestamp))
            series_map[sk]=series
            times_map[sk]=[c.timestamp for c in series]

        n1=[]
        n5=[]
        option_history=defaultdict(list)
        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5,spot,5)
            for side,row in options_at_time.get(spot.timestamp,[]):
                option_history[(side,int(row.strike))].append(_row_to_candle(row))
            if not (time(9,35) <= spot.timestamp.time() <= time(13,30)):
                continue
            if len(n1)<25 or len(n5)<5:
                continue
            spot_atr=atr(n1[-20:])
            if spot_atr<=0:
                continue
            closes5=[c.close for c in n5[-12:]]
            fast=ema(closes5,5)
            slow=ema(closes5,9)
            gap=abs(fast-slow)/spot_atr
            spot_move=(n1[-1].close-n1[-4].close)/spot_atr
            direction=''
            if fast>slow and n5[-1].close>fast and spot_move>0 and n1[-1].close>n1[-1].open:
                direction='CE'
            elif fast<slow and n5[-1].close<fast and spot_move<0 and n1[-1].close<n1[-1].open:
                direction='PE'
                spot_move=-spot_move
            if not direction:
                continue
            strike=_nearest_common_strike(strikes_by_side,spot.close)
            if strike is None:
                continue
            chosen=option_history.get((direction,strike),[])
            opposite_side='PE' if direction=='CE' else 'CE'
            opposite=option_history.get((opposite_side,strike),[])
            if len(chosen)<15 or len(opposite)<5:
                continue
            chosen_move=move_pct(chosen,3)
            opposite_move=move_pct(opposite,3)
            closes=[c.close for c in chosen[-20:]]
            option_fast=ema(closes,5)
            option_slow=ema(closes,13)
            events.append(Event(
                day=day_value,
                signal_time=spot.timestamp,
                direction=direction,
                strike=strike,
                spot_trend_gap_atr=gap,
                spot_move_atr=spot_move,
                option_move_pct=chosen_move,
                option_outperformance_pct=chosen_move-opposite_move,
                option_volume_ratio=volume_ratio(chosen),
                option_ema_aligned=chosen[-1].close>option_fast>option_slow,
                option_premium=chosen[-1].close,
                option_oi_change_pct=oi_change(chosen,3),
                option_high=chosen[-1].high,
                series_key=(day_value,direction,strike),
            ))
    events.sort(key=lambda e:e.signal_time)
    return events,series_map,times_map


def qualifies(e:Event,c:Candidate)->bool:
    return (
        e.spot_trend_gap_atr>=c.min_spot_gap_atr
        and e.spot_move_atr>=c.min_spot_move_atr
        and e.option_move_pct>=c.min_option_move_pct
        and e.option_outperformance_pct>=c.min_outperformance_pct
        and e.option_volume_ratio>=c.min_volume_ratio
        and (not c.require_option_ema or e.option_ema_aligned)
        and e.option_premium>=c.min_premium
        and e.option_oi_change_pct>=-15.0
    )


def simulate(e:Event,c:Candidate,series,times):
    start=bisect_right(times,e.signal_time)
    trigger=e.option_high+0.10
    deadline=e.signal_time+timedelta(minutes=1)
    entry_index=-1
    entry=0.0
    for idx in range(start,len(series)):
        candle=series[idx]
        if candle.timestamp.date()!=e.day or candle.timestamp>deadline:
            break
        if candle.high>=trigger:
            entry_index=idx
            entry=max(trigger,candle.open)+0.20
            break
    if entry_index<0:
        return None
    stop=max(0.05,entry-c.stop_points)
    target=entry+c.target_points
    entry_time=series[entry_index].timestamp
    cutoff=entry_time+timedelta(minutes=20)
    future=[x for x in series[entry_index:] if x.timestamp.date()==e.day and x.timestamp<=cutoff]
    if not future:
        return None
    for candle in future:
        if candle.low<=stop:
            return Trade(e.day,e.signal_time,entry_time,e.direction,e.strike,'LOSS',-c.stop_points-0.50)
        if candle.high>=target:
            return Trade(e.day,e.signal_time,entry_time,e.direction,e.strike,'WIN',c.target_points-0.50)
    return Trade(e.day,e.signal_time,entry_time,e.direction,e.strike,'TIMEOUT',future[-1].close-entry-0.50)


def stats_for(trades):
    trades=tuple(trades)
    wins=sum(t.outcome=='WIN' for t in trades)
    losses=sum(t.outcome=='LOSS' for t in trades)
    timeouts=sum(t.outcome=='TIMEOUT' for t in trades)
    n=len(trades)
    net=sum(t.net_points for t in trades)
    gains=sum(max(0,t.net_points) for t in trades)
    loss_value=abs(sum(min(0,t.net_points) for t in trades))
    pf=gains/loss_value if loss_value else (gains if gains else 0)
    equity=peak=dd=0.0
    for t in trades:
        equity+=t.net_points
        peak=max(peak,equity)
        dd=max(dd,peak-equity)
    return Stats(n,wins,losses,timeouts,round(100*wins/n,2) if n else 0.0,round(net,2),round(net/n,2) if n else 0.0,round(pf,2),round(dd,2))


def evaluate(events,candidate,series_map,times_map,start_day,end_day):
    by_day=defaultdict(list)
    for e in events:
        if start_day<=e.day<=end_day and qualifies(e,candidate):
            by_day[e.day].append(e)
    trades=[]
    for d in sorted(by_day):
        for e in by_day[d]:
            series=series_map.get(e.series_key); times=times_map.get(e.series_key)
            if not series or not times: continue
            trade=simulate(e,candidate,series,times)
            if trade is not None:
                trades.append(trade)
                break
    return stats_for(trades),tuple(trades)


def grid():
    result=[]
    exits=[(3.0,5.0),(4.0,6.0),(5.0,7.0),(5.0,5.0)]
    for gap,spotmove,optmove,outperf,vol,req,premium,pair in itertools.product(
        (0.05,0.12),(0.20,0.45),(0.5,1.5),(0.5,1.2),(1.0,1.4),(False,True),(30.0,50.0),exits
    ):
        result.append(Candidate(gap,spotmove,optmove,outperf,vol,req,premium,pair[0],pair[1]))
    return result


def rank_score(s:Stats):
    if s.trades<35 or s.expectancy<=0 or s.profit_factor<=1.0:
        return -1e9
    p=s.wins/s.trades; z=1.0
    lower=(p+z*z/(2*s.trades)-z*math.sqrt((p*(1-p)+z*z/(4*s.trades))/s.trades))/(1+z*z/s.trades)
    return lower*100+min(s.profit_factor,3)*5+min(s.expectancy,4)*2-s.max_drawdown/60


def month_end(year:int,month:int)->date:
    if month==12: return date(year,12,31)
    return date(year,month+1,1)-timedelta(days=1)


def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot_df=_parse_spot_frame(path); opt_df=_parse_option_frame(path)
    lo,hi=date(2025,7,1),date(2026,6,30)
    spot_df=spot_df[(spot_df['timestamp'].dt.date>=lo)&(spot_df['timestamp'].dt.date<=hi)]
    opt_df=opt_df[(opt_df['day']>=lo)&(opt_df['day']<=hi)]
    spot_by_day={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot_df.groupby(spot_df['timestamp'].dt.date,sort=True)}
    option_rows=defaultdict(list)
    for r in opt_df.itertuples(index=False): option_rows[r.day].append(r)
    events,series_map,times_map=build_events(spot_by_day,option_rows)
    candidates=grid()

    test_months=[(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]
    folds=[]; all_oos=[]
    for y,m in test_months:
        test_start=date(y,m,1); test_end=month_end(y,m)
        train_end=test_start-timedelta(days=1)
        ranked=[]
        for c in candidates:
            s,_=evaluate(events,c,series_map,times_map,lo,train_end)
            score=rank_score(s)
            if score>-1e8: ranked.append((score,c,s))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            folds.append({'month':test_start.strftime('%Y-%m'),'status':'NO_TRAINING_CANDIDATE'})
            continue
        _,chosen,train_stats=ranked[0]
        test_stats,test_trades=evaluate(events,chosen,series_map,times_map,test_start,test_end)
        all_oos.extend(test_trades)
        folds.append({'month':test_start.strftime('%Y-%m'),'candidate_frozen_before_month':True,'candidate':asdict(chosen),'training':asdict(train_stats),'test':asdict(test_stats),'trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in test_trades]})
    aggregate=stats_for(all_oos)
    positive_folds=sum(1 for f in folds if 'test' in f and f['test']['expectancy']>0)
    sixty_folds=sum(1 for f in folds if 'test' in f and f['test']['win_rate']>=60)
    passed=aggregate.trades>=40 and aggregate.win_rate>=70 and aggregate.expectancy>0 and aggregate.profit_factor>1.20 and positive_folds>=6 and sixty_folds>=6
    payload={'search_name':'Shiv Option-Impulse Monthly Walk-Forward','candidate_count':len(candidates),'events_built':len(events),'method':'For each test month, select exactly one candidate using only all earlier data, then freeze and trade the next month. Aggregate Oct 2025-Jun 2026 out-of-sample trades.','proof_rule':'>=40 aggregate OOS trades, >=70% target wins, positive expectancy, PF>1.20, >=6 positive-expectancy months and >=6 months with >=60% wins.','status':'PROVEN_70_PLUS' if passed else 'NO_70_PLUS_CANDIDATE_PROVEN','aggregate_oos':asdict(aggregate),'positive_expectancy_folds':positive_folds,'sixty_plus_win_folds':sixty_folds,'folds':folds}
    Path('strategy_70_option_impulse.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(payload,indent=2))


if __name__=='__main__': main()
