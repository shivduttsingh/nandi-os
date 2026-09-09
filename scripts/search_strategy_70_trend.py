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
    trend_gap_atr: float
    pullback_depth_atr: float
    impulse_atr: float
    option_premium: float
    option_volume_ratio: float
    option_outperformance: float
    option_ema_aligned: bool
    option_oi_change: float
    option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    min_trend_gap_atr: float
    max_pullback_atr: float
    min_impulse_atr: float
    max_impulse_atr: float
    min_volume_ratio: float
    min_outperformance: float
    require_option_ema: bool
    min_premium: float
    trigger_window: int
    target_points: float
    stop_points: float
    hold_minutes: int


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
    profitable_rate: float
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
        result = alpha * value + (1 - alpha) * result
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


def vol_ratio(candles: list[IntradayCandle], lookback: int = 12) -> float:
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def move(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


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
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(candle)
            strikes_by_side[side].add(strike)
        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            continue
        for key, values in option_lists.items():
            sk = (day_value, key[0], key[1])
            series = tuple(sorted(values, key=lambda c: c.timestamp))
            series_map[sk] = series
            times_map[sk] = [c.timestamp for c in series]

        n1 = []
        n5 = []
        option_history = defaultdict(list)
        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            for side, row in options_at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))
            if not (time(9, 50) <= spot.timestamp.time() <= time(13, 30)):
                continue
            if len(n1) < 35 or len(n5) < 8:
                continue
            spot_atr = atr(n1[-25:])
            if spot_atr <= 0:
                continue
            five_closes = [c.close for c in n5[-12:]]
            fast = ema(five_closes, 5)
            slow = ema(five_closes, 9)
            current, prev = n1[-1], n1[-2]
            impulse = abs(current.close - n1[-16].close) / spot_atr if len(n1) >= 16 else 0.0

            direction = ""
            trend_gap = abs(fast - slow) / spot_atr
            pullback = 999.0
            if (
                fast > slow
                and n5[-1].close > fast
                and n5[-1].close > n5[-2].close
                and prev.close <= prev.open
                and current.close > current.open
                and current.close > prev.high
                and current.close > fast
            ):
                direction = "CE"
                pullback = max(0.0, fast - prev.low) / spot_atr
            elif (
                fast < slow
                and n5[-1].close < fast
                and n5[-1].close < n5[-2].close
                and prev.close >= prev.open
                and current.close < current.open
                and current.close < prev.low
                and current.close < fast
            ):
                direction = "PE"
                pullback = max(0.0, prev.high - fast) / spot_atr
            if not direction:
                continue

            strike = _nearest_common_strike(strikes_by_side, spot.close)
            if strike is None:
                continue
            chosen = option_history.get((direction, strike), [])
            opposite_side = "PE" if direction == "CE" else "CE"
            opposite = option_history.get((opposite_side, strike), [])
            if len(chosen) < 15 or len(opposite) < 5:
                continue
            closes = [c.close for c in chosen[-20:]]
            option_fast = ema(closes, 5)
            option_slow = ema(closes, 13)
            chosen_move = move(chosen, 3)
            opposite_move = move(opposite, 3)
            events.append(Event(
                day=day_value,
                signal_time=spot.timestamp,
                direction=direction,
                strike=strike,
                trend_gap_atr=trend_gap,
                pullback_depth_atr=pullback,
                impulse_atr=impulse,
                option_premium=chosen[-1].close,
                option_volume_ratio=vol_ratio(chosen),
                option_outperformance=chosen_move - opposite_move,
                option_ema_aligned=chosen[-1].close > option_fast > option_slow,
                option_oi_change=oi_change(chosen, 3),
                option_high=chosen[-1].high,
                series_key=(day_value, direction, strike),
            ))
    events.sort(key=lambda e: e.signal_time)
    return events, series_map, times_map


def qualifies(e: Event, c: Candidate) -> bool:
    return (
        e.trend_gap_atr >= c.min_trend_gap_atr
        and e.pullback_depth_atr <= c.max_pullback_atr
        and c.min_impulse_atr <= e.impulse_atr <= c.max_impulse_atr
        and e.option_volume_ratio >= c.min_volume_ratio
        and e.option_outperformance >= c.min_outperformance
        and (not c.require_option_ema or e.option_ema_aligned)
        and e.option_premium >= c.min_premium
        and e.option_oi_change >= -15.0
    )


def simulate(e: Event, c: Candidate, series, times):
    start = bisect_right(times, e.signal_time)
    trigger = e.option_high + 0.10
    deadline = e.signal_time + timedelta(minutes=c.trigger_window)
    entry_index = -1
    entry = 0.0
    for idx in range(start, len(series)):
        candle = series[idx]
        if candle.timestamp.date() != e.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            entry_index = idx
            entry = max(trigger, candle.open) + 0.20
            break
    if entry_index < 0:
        return None
    stop = max(0.05, entry - c.stop_points)
    target = entry + c.target_points
    entry_time = series[entry_index].timestamp
    cutoff = entry_time + timedelta(minutes=c.hold_minutes)
    future = [x for x in series[entry_index:] if x.timestamp.date() == e.day and x.timestamp <= cutoff]
    if not future:
        return None
    for candle in future:
        if candle.low <= stop:
            return Trade(e.day, e.signal_time, entry_time, e.direction, e.strike, "LOSS", -c.stop_points - 0.50)
        if candle.high >= target:
            return Trade(e.day, e.signal_time, entry_time, e.direction, e.strike, "WIN", c.target_points - 0.50)
    return Trade(e.day, e.signal_time, entry_time, e.direction, e.strike, "TIMEOUT", future[-1].close - entry - 0.50)


def stats_for(trades):
    trades = tuple(trades)
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timeouts = sum(t.outcome == "TIMEOUT" for t in trades)
    n = len(trades)
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    equity = peak = dd = 0.0
    for t in trades:
        equity += t.net_points
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return Stats(n, wins, losses, timeouts, round(100*wins/n,2) if n else 0.0, round(100*sum(t.net_points>0 for t in trades)/n,2) if n else 0.0, round(net,2), round(net/n,2) if n else 0.0, round(pf,2), round(dd,2))


def evaluate(events, candidate, series_map, times_map, start_day, end_day):
    by_day = defaultdict(list)
    for event in events:
        if start_day <= event.day <= end_day and qualifies(event, candidate):
            by_day[event.day].append(event)
    trades = []
    for day_value in sorted(by_day):
        for event in by_day[day_value]:
            series = series_map.get(event.series_key)
            times = times_map.get(event.series_key)
            if not series or not times:
                continue
            trade = simulate(event, candidate, series, times)
            if trade is not None:
                trades.append(trade)
                break
    return stats_for(trades), tuple(trades)


def grid():
    exit_pairs = [(4.0,6.0),(5.0,7.0),(6.0,8.0),(6.0,6.0),(8.0,8.0),(8.0,6.0)]
    result=[]
    for v in itertools.product((0.05,0.10,0.18),(0.25,0.50,0.80),(0.5,0.8,1.1),(1.8,2.5,3.5),(0.8,1.1,1.4),(0.25,0.75,1.25),(False,True),(25.0,40.0,60.0),(1,2,3),exit_pairs,(20,30)):
        trend,pull,minimp,maximp,vol,outperf,req,premium,trigger,pair,hold=v
        result.append(Candidate(trend,pull,minimp,maximp,vol,outperf,req,premium,trigger,pair[0],pair[1],hold))
    return result


def rank_score(s: Stats):
    if s.trades < 35 or s.expectancy <= 0 or s.profit_factor <= 1.0:
        return -1e9
    p=s.wins/s.trades
    z=1.0
    lower=(p+z*z/(2*s.trades)-z*math.sqrt((p*(1-p)+z*z/(4*s.trades))/s.trades))/(1+z*z/s.trades)
    return lower*100+min(s.profit_factor,3)*5+min(s.expectancy,5)*2-s.max_drawdown/50


def proof(s: Stats):
    return s.trades>=14 and s.win_rate>=70 and s.expectancy>0 and s.profit_factor>1.15


def stress(s: Stats):
    return s.trades>=7 and s.win_rate>=60 and s.expectancy>0 and s.profit_factor>1.0


def main():
    path=_download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot_df=_parse_spot_frame(path)
    opt_df=_parse_option_frame(path)
    lo,hi=date(2025,7,1),date(2026,6,30)
    spot_df=spot_df[(spot_df['timestamp'].dt.date>=lo)&(spot_df['timestamp'].dt.date<=hi)]
    opt_df=opt_df[(opt_df['day']>=lo)&(opt_df['day']<=hi)]
    spot_by_day={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot_df.groupby(spot_df['timestamp'].dt.date,sort=True)}
    option_rows=defaultdict(list)
    for r in opt_df.itertuples(index=False): option_rows[r.day].append(r)
    events,series_map,times_map=build_events(spot_by_day,option_rows)
    candidates=grid()
    rounds=[
        ((date(2025,7,1),date(2025,12,31)),(date(2026,1,1),date(2026,2,28)),(date(2026,3,1),date(2026,3,31))),
        ((date(2025,7,1),date(2026,2,28)),(date(2026,3,1),date(2026,4,30)),(date(2026,5,1),date(2026,5,31))),
        ((date(2025,7,1),date(2026,4,30)),(date(2026,5,1),date(2026,6,30)),None),
    ]
    output=[]
    proven=None
    for i,(train_w,proof_w,stress_w) in enumerate(rounds,1):
        ranked=[]
        for c in candidates:
            s,_=evaluate(events,c,series_map,times_map,*train_w)
            score=rank_score(s)
            if score>-1e8: ranked.append((score,c,s))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            output.append({'round':i,'status':'NO_TRAINING_CANDIDATE'})
            continue
        _,c,train_s=ranked[0]
        proof_s,proof_trades=evaluate(events,c,series_map,times_map,*proof_w)
        stress_s=Stats(0,0,0,0,0,0,0,0,0,0)
        stress_trades=()
        stress_ok=True
        if stress_w:
            stress_s,stress_trades=evaluate(events,c,series_map,times_map,*stress_w)
            stress_ok=stress(stress_s)
        passed=proof(proof_s) and stress_ok
        row={'round':i,'candidate_frozen_before_proof':True,'candidate':asdict(c),'training_window':[x.isoformat() for x in train_w],'proof_window':[x.isoformat() for x in proof_w],'stress_window':[x.isoformat() for x in stress_w] if stress_w else None,'training':asdict(train_s),'proof':asdict(proof_s),'stress':asdict(stress_s),'passed':passed,'proof_trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in proof_trades],'stress_trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in stress_trades]}
        output.append(row)
        if passed:
            proven=row
            break
    payload={'search_name':'Shiv Trend-Pullback Strategy 70 Search','candidate_count':len(candidates),'events_built':len(events),'anti_cherry_pick_rule':'One candidate is selected from training only before each proof window is opened. Failed proof periods are only used in later rolling rounds.','status':'PROVEN_70_PLUS' if proven else 'NO_70_PLUS_CANDIDATE_PROVEN','proven_candidate':proven,'rounds':output}
    Path('strategy_70_trend.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(payload,indent=2))


if __name__=='__main__': main()
