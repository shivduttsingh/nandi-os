from __future__ import annotations

import itertools
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, time, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nandi_oi.models import IntradayCandle
from test1.public_backtest import _download_public_sample, _nearest_common_strike, _parse_option_frame, _parse_spot_frame, _row_to_candle, _update_aggregate


@dataclass(frozen=True)
class Event:
    day: date
    signal_time: object
    direction: str
    strike: int
    same_color: bool
    second_body_ratio: float
    second_range_points: float
    total_move_bps: float
    option_move_pct: float
    option_outperformance_pct: float
    option_volume_ratio: float
    option_oi_change_pct: float
    option_premium: float
    option_high: float
    series_key: tuple[date, str, int]


@dataclass(frozen=True)
class Candidate:
    require_same_color: bool
    min_second_body_ratio: float
    min_second_range_points: float
    min_total_move_bps: float
    min_option_move_pct: float
    min_outperformance_pct: float
    min_volume_ratio: float
    min_oi_change_pct: float
    min_premium: float
    target_points: float
    stop_points: float
    max_hold_minutes: int


@dataclass(frozen=True)
class Trade:
    day: date
    signal_time: object
    direction: str
    strike: int
    outcome: str
    net_points: float


def pct(a, b):
    return ((b / a) - 1.0) * 100.0 if a > 0 else 0.0


def oi_change(candles, lookback=3):
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    return pct(old, candles[-1].open_interest) if old > 0 else 0.0


def volume_ratio(candles):
    history = [c.volume for c in candles[-7:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def move(candles, lookback=3):
    if len(candles) < lookback + 1:
        return 0.0
    return pct(candles[-lookback - 1].close, candles[-1].close)


def build_events(spot_by_day, option_rows_by_day):
    events = []
    series_map = {}
    times_map = {}
    for day_value in sorted(spot_by_day):
        spot_day = spot_by_day[day_value]
        raw = option_rows_by_day.get(day_value, [])
        if len(spot_day) < 30 or not raw:
            continue
        at_time = defaultdict(list)
        option_lists = defaultdict(list)
        strikes = {"CE": set(), "PE": set()}
        for row in raw:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(candle)
            strikes[side].add(strike)
        if not (strikes["CE"] & strikes["PE"]):
            continue
        for key, values in option_lists.items():
            sk = (day_value, key[0], key[1])
            series = tuple(sorted(values, key=lambda c: c.timestamp))
            series_map[sk] = series
            times_map[sk] = [c.timestamp for c in series]

        n3 = []
        option_history = defaultdict(list)
        emitted = False
        for spot in spot_day:
            _update_aggregate(n3, spot, 3)
            for side, row in at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))
            if emitted or spot.timestamp.time() != time(9, 20):
                continue
            if len(n3) < 2:
                continue
            first, second = n3[-2], n3[-1]
            first_up = first.close > first.open
            second_up = second.close > second.open
            if second.close == second.open:
                continue
            direction = "CE" if second_up else "PE"
            same_color = first_up == second_up
            rng = max(second.high - second.low, 1e-9)
            body_ratio = abs(second.close - second.open) / rng
            total_move_bps = abs(second.close - first.open) / max(first.open, 1.0) * 10000.0
            strike = _nearest_common_strike(strikes, spot.close)
            if strike is None:
                continue
            chosen = option_history.get((direction, strike), [])
            opposite = option_history.get(("PE" if direction == "CE" else "CE", strike), [])
            if len(chosen) < 5 or len(opposite) < 5:
                continue
            chosen_move = move(chosen, 3)
            opposite_move = move(opposite, 3)
            events.append(Event(
                day_value, spot.timestamp, direction, strike, same_color, body_ratio,
                second.high - second.low, total_move_bps, chosen_move,
                chosen_move - opposite_move, volume_ratio(chosen), oi_change(chosen, 3),
                chosen[-1].close, chosen[-1].high, (day_value, direction, strike),
            ))
            emitted = True
    return events, series_map, times_map


def qualifies(e, c):
    return (
        (not c.require_same_color or e.same_color)
        and e.second_body_ratio >= c.min_second_body_ratio
        and e.second_range_points >= c.min_second_range_points
        and e.total_move_bps >= c.min_total_move_bps
        and e.option_move_pct >= c.min_option_move_pct
        and e.option_outperformance_pct >= c.min_outperformance_pct
        and e.option_volume_ratio >= c.min_volume_ratio
        and e.option_oi_change_pct >= c.min_oi_change_pct
        and e.option_premium >= c.min_premium
    )


def simulate(e, c, series, times):
    start = bisect_right(times, e.signal_time)
    trigger = e.option_high + 0.10
    deadline = e.signal_time + timedelta(minutes=2)
    idx = -1
    entry = 0.0
    for i in range(start, len(series)):
        candle = series[i]
        if candle.timestamp.date() != e.day or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            idx = i
            entry = max(trigger, candle.open) + 0.20
            break
    if idx < 0:
        return None
    stop = max(0.05, entry - c.stop_points)
    target = entry + c.target_points
    cutoff = series[idx].timestamp + timedelta(minutes=c.max_hold_minutes)
    future = [x for x in series[idx:] if x.timestamp.date() == e.day and x.timestamp <= cutoff]
    if not future:
        return None
    for candle in future:
        if candle.low <= stop:
            return Trade(e.day, e.signal_time, e.direction, e.strike, "LOSS", -c.stop_points - 0.50)
        if candle.high >= target:
            return Trade(e.day, e.signal_time, e.direction, e.strike, "WIN", c.target_points - 0.50)
    return Trade(e.day, e.signal_time, e.direction, e.strike, "TIMEOUT", future[-1].close - entry - 0.50)


def stats_for(trades):
    trades = tuple(trades)
    n = len(trades)
    wins = sum(t.outcome == "WIN" for t in trades)
    losses = sum(t.outcome == "LOSS" for t in trades)
    timeouts = sum(t.outcome == "TIMEOUT" for t in trades)
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t.net_points
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"trades": n, "wins": wins, "losses": losses, "timeouts": timeouts, "win_rate": round(100*wins/n,2) if n else 0.0, "net_points": round(net,2), "expectancy": round(net/n,2) if n else 0.0, "profit_factor": round(pf,2), "max_drawdown": round(dd,2)}


def evaluate(events, candidate, series_map, times_map, start_day, end_day):
    trades = []
    for e in events:
        if not (start_day <= e.day <= end_day) or not qualifies(e, candidate):
            continue
        series = series_map.get(e.series_key)
        times = times_map.get(e.series_key)
        if not series or not times:
            continue
        trade = simulate(e, candidate, series, times)
        if trade is not None:
            trades.append(trade)
    return stats_for(trades), tuple(trades)


def grid(selective=False):
    exits = ((6.0,4.0,30),(8.0,5.0,35),(10.0,6.0,40)) if selective else ((3.0,4.0,20),(4.0,5.0,25),(4.0,4.0,25))
    out = []
    for v in itertools.product(
        (False, True),
        (0.35, 0.55) if not selective else (0.55, 0.70),
        (8.0, 15.0) if not selective else (12.0, 20.0),
        (3.0, 6.0) if not selective else (5.0, 8.0),
        (0.0, 0.6) if not selective else (0.6, 1.2),
        (0.3, 0.8) if not selective else (0.8, 1.5),
        (0.8, 1.2) if not selective else (1.0, 1.4),
        (-10.0, 0.0),
        (30.0, 50.0),
        exits,
    ):
        out.append(Candidate(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9][0],v[9][1],v[9][2]))
    return out


def rank_score(s, selective):
    minimum = 12 if selective else 25
    if s["trades"] < minimum or s["expectancy"] <= 0 or s["profit_factor"] <= 1:
        return -1e9
    p = s["wins"] / s["trades"]
    z = 1.0
    lower = (p + z*z/(2*s["trades"]) - z*math.sqrt((p*(1-p)+z*z/(4*s["trades"]))/s["trades"])) / (1 + z*z/s["trades"])
    return lower*100 + min(s["profit_factor"],4)*5 + min(s["expectancy"],5)*(4 if selective else 2) - s["max_drawdown"]/60


def month_end(y,m):
    return date(y,12,31) if m==12 else date(y,m+1,1)-timedelta(days=1)


def walk(name, candidates, events, series_map, times_map, lo, selective):
    folds=[]; all_oos=[]
    for y,m in ((2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)):
        start=date(y,m,1); end=month_end(y,m); ranked=[]
        for c in candidates:
            tr,_=evaluate(events,c,series_map,times_map,lo,start-timedelta(days=1)); score=rank_score(tr,selective)
            if score>-1e8: ranked.append((score,c,tr))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            folds.append({"month":start.strftime("%Y-%m"),"status":"NO_TRAINING_CANDIDATE"}); continue
        _,c,tr=ranked[0]; te,tt=evaluate(events,c,series_map,times_map,start,end); all_oos.extend(tt)
        folds.append({"month":start.strftime("%Y-%m"),"candidate_frozen_before_month":True,"candidate":asdict(c),"training":tr,"test":te,"trades":[{**asdict(t),"day":t.day.isoformat(),"signal_time":t.signal_time.isoformat()} for t in tt]})
    ag=stats_for(all_oos); pos=sum(1 for f in folds if "test" in f and f["test"]["expectancy"]>0); sixty=sum(1 for f in folds if "test" in f and f["test"]["win_rate"]>=60); active=sum(1 for f in folds if "test" in f and f["test"]["trades"]>0); avg=ag["trades"]/active if active else 0
    if selective:
        passed=14<=ag["trades"]<=36 and ag["win_rate"]>=70 and ag["expectancy"]>=1.5 and ag["profit_factor"]>=1.5 and pos>=5 and sixty>=5 and avg<=4
    else:
        passed=ag["trades"]>=30 and ag["win_rate"]>=70 and ag["expectancy"]>0 and ag["profit_factor"]>=1.2 and pos>=5 and sixty>=5
    return {"name":name,"status":"PROVEN_70_PLUS" if passed else "NO_70_PLUS_CANDIDATE_PROVEN","aggregate_oos":ag,"positive_months":pos,"sixty_plus_months":sixty,"active_months":active,"avg_trades_per_active_month":round(avg,2),"folds":folds}


def main():
    path=_download_public_sample(Path("/tmp/shiv_strategy70/nifty_1y_1min.xlsx")); spot=_parse_spot_frame(path); options=_parse_option_frame(path); lo,hi=date(2025,7,1),date(2026,6,30); spot=spot[(spot.timestamp.dt.date>=lo)&(spot.timestamp.dt.date<=hi)]; options=options[(options.day>=lo)&(options.day<=hi)]
    spot_by_day={d:tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot.groupby(spot.timestamp.dt.date,sort=True)}; rows=defaultdict(list)
    for row in options.itertuples(index=False): rows[row.day].append(row)
    events,series_map,times_map=build_events(spot_by_day,rows); accuracy=walk("Morning Accuracy",grid(False),events,series_map,times_map,lo,False); selective=walk("Morning Selective Profit",grid(True),events,series_map,times_map,lo,True)
    payload={"search_name":"Shiv First-Two-3m Morning Monthly Walk-Forward","events_built":len(events),"accuracy":accuracy,"selective_profit":selective,"overall_status":"BOTH_PROVEN" if accuracy["status"]=="PROVEN_70_PLUS" and selective["status"]=="PROVEN_70_PLUS" else "NOT_BOTH_PROVEN"}; Path("strategy_70_morning.json").write_text(json.dumps(payload,indent=2),encoding="utf-8"); print(json.dumps(payload,indent=2))

if __name__=="__main__": main()
