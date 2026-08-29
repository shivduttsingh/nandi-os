from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
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
    impulse_level: int
    pullback_level: int
    reversal_level: int
    min_score: float
    agree_window_minutes: int
    failure_window_minutes: int
    target_points: float
    stop_points: float
    cooldown_minutes: int
    max_trades_day: int = 5


@dataclass(frozen=True)
class Signal:
    family: str
    event: object
    score: float


@dataclass(frozen=True)
class Trade:
    day: date
    trigger_type: str
    family: str
    signal_time: object
    entry_time: object
    direction: str
    strike: int
    outcome: str
    net_points: float


def score_event(family: str, event, cfg: Config):
    if family == 'IMPULSE':
        return base.impulse_pass_score(event, cfg.impulse_level)
    if family == 'PULLBACK':
        return base.pullback_pass_score(event, cfg.pullback_level)
    return base.reversal_pass_score(event, cfg.reversal_level)


def simulate_signal(signal: Signal, cfg: Config, maps, trigger_type: str):
    wrapper = base.Wrapped(signal.family, signal.event, signal.score)
    synthetic = base.Config(
        cfg.impulse_level,
        cfg.pullback_level,
        cfg.reversal_level,
        cfg.min_score,
        cfg.target_points,
        cfg.stop_points,
        cfg.cooldown_minutes,
        5,
        cfg.max_trades_day,
    )
    tr = base.simulate_wrapped(wrapper, synthetic, maps)
    if tr is None:
        return None
    return Trade(
        tr.day,
        trigger_type,
        tr.family,
        tr.signal_time,
        tr.entry_time,
        tr.direction,
        tr.strike,
        tr.outcome,
        tr.net_points,
    )


def stats(trades):
    t = tuple(trades)
    n = len(t)
    wins = sum(x.outcome == 'WIN' for x in t)
    losses = sum(x.outcome == 'LOSS' for x in t)
    timeouts = n - wins - losses
    net = sum(x.net_points for x in t)
    gains = sum(max(0.0, x.net_points) for x in t)
    lv = abs(sum(min(0.0, x.net_points) for x in t))
    pf = gains / lv if lv else (gains if gains else 0.0)
    eq = peak = dd = 0.0
    for x in t:
        eq += x.net_points
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        'trades': n,
        'wins': wins,
        'losses': losses,
        'timeouts': timeouts,
        'win_rate': round(100.0 * wins / n, 2) if n else 0.0,
        'net_points': round(net, 2),
        'expectancy': round(net / n, 2) if n else 0.0,
        'profit_factor': round(pf, 2),
        'max_drawdown': round(dd, 2),
    }


def daily(trades, trading_days, start_day, end_day):
    days = [d for d in trading_days if start_day <= d <= end_day]
    counts = defaultdict(int)
    net = defaultdict(float)
    types = defaultdict(int)
    families = defaultdict(int)
    for t in trades:
        counts[t.day] += 1
        net[t.day] += t.net_points
        types[t.trigger_type] += 1
        families[t.family] += 1
    n = len(days)
    return {
        'trading_days': n,
        'avg_trades_per_day': round(len(trades) / n, 2) if n else 0.0,
        'avg_net_points_per_day': round(sum(net.values()) / n, 2) if n else 0.0,
        'pct_days_with_3plus_trades': round(100 * sum(counts[d] >= 3 for d in days) / n, 2) if n else 0.0,
        'pct_profitable_days': round(100 * sum(net[d] > 0 for d in days) / n, 2) if n else 0.0,
        'pct_days_15plus_points': round(100 * sum(net[d] >= 15 for d in days) / n, 2) if n else 0.0,
        'max_trades_day': max((counts[d] for d in days), default=0),
        'trigger_totals': dict(types),
        'family_totals': dict(families),
    }


def evaluate(cfg, streams, maps, trading_days, start_day, end_day):
    by_day = defaultdict(list)
    for family, events in streams.items():
        for e in events:
            if not (start_day <= e.day <= end_day):
                continue
            s = score_event(family, e, cfg)
            if s is not None and s >= cfg.min_score:
                by_day[e.day].append(Signal(family, e, s))

    trades = []
    for d in [x for x in trading_days if start_day <= x <= end_day]:
        signals = sorted(by_day.get(d, []), key=lambda x: x.event.signal_time)
        recent = []
        last_entry = None
        fired_keys = set()
        for cur in signals:
            now = cur.event.signal_time
            max_age = max(cfg.agree_window_minutes, cfg.failure_window_minutes)
            recent = [r for r in recent if 0 <= (now - r.event.signal_time).total_seconds() <= max_age * 60]

            trigger = None
            partner = None
            if cur.family in {'IMPULSE', 'PULLBACK'}:
                other_family = 'PULLBACK' if cur.family == 'IMPULSE' else 'IMPULSE'
                candidates = [
                    r for r in recent
                    if r.family == other_family
                    and r.event.direction == cur.event.direction
                    and (now - r.event.signal_time).total_seconds() <= cfg.agree_window_minutes * 60
                ]
                if candidates:
                    partner = max(candidates, key=lambda r: (r.score, r.event.signal_time))
                    trigger = 'AGREEMENT'
            elif cur.family == 'REVERSAL':
                candidates = [
                    r for r in recent
                    if r.family in {'IMPULSE', 'PULLBACK'}
                    and r.event.direction != cur.event.direction
                    and (now - r.event.signal_time).total_seconds() <= cfg.failure_window_minutes * 60
                ]
                if candidates:
                    partner = max(candidates, key=lambda r: (r.score, r.event.signal_time))
                    trigger = 'FAILED_TREND_REVERSAL'

            recent.append(cur)
            if trigger is None or partner is None:
                continue
            if len([t for t in trades if t.day == d]) >= cfg.max_trades_day:
                break
            if last_entry is not None and (now - last_entry).total_seconds() < cfg.cooldown_minutes * 60:
                continue
            event_key = (cur.family, cur.event.signal_time, cur.event.direction, cur.event.strike, trigger)
            if event_key in fired_keys:
                continue

            # Require the pair itself to be strong, not merely each leg barely above threshold.
            pair_score = (cur.score + partner.score) / 2.0
            if pair_score < cfg.min_score + 0.03:
                continue

            tr = simulate_signal(cur, cfg, maps, trigger)
            if tr is None:
                continue
            trades.append(tr)
            last_entry = tr.entry_time
            fired_keys.add(event_key)

    st = stats(trades)
    ds = daily(trades, trading_days, start_day, end_day)
    return st, ds, tuple(trades)


def grid():
    levels = (
        (0,0,0),(1,0,0),(0,1,0),(0,0,1),
        (1,1,0),(1,0,1),(0,1,1),(1,1,1),
    )
    exits = ((6.0,4.0),(8.0,4.0),(8.0,5.0),(10.0,5.0))
    for lev, score, aw, fw, pair, cd in itertools.product(
        levels,
        (0.40,0.50,0.60),
        (3,6),
        (6,12),
        exits,
        (5,8),
    ):
        yield Config(lev[0],lev[1],lev[2],score,aw,fw,pair[0],pair[1],cd)


def rank_score(st, ds):
    n = st['trades']
    if ds['trading_days'] == 0 or n < ds['trading_days'] * 1.5:
        return -1e9
    if ds['avg_trades_per_day'] < 1.5 or ds['pct_days_with_3plus_trades'] < 25:
        return -1e9
    if st['expectancy'] <= 0 or st['profit_factor'] <= 1:
        return -1e9
    p = st['wins'] / n
    z = 1.0
    lb = (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / (1 + z*z/n)
    return (
        lb * 100
        + min(st['profit_factor'], 4) * 5
        + min(st['expectancy'], 8) * 2
        + min(ds['avg_net_points_per_day'], 25)
        + ds['pct_profitable_days'] / 10
        + ds['pct_days_with_3plus_trades'] / 20
        - st['max_drawdown'] / 150
    )


def period(cfg, streams, maps, days, start_day, end_day, include_trades=True):
    st, ds, tr = evaluate(cfg, streams, maps, days, start_day, end_day)
    out = {'stats': st, 'daily': ds}
    if include_trades:
        out['trades'] = [
            {**asdict(t), 'day': t.day.isoformat(), 'signal_time': t.signal_time.isoformat(), 'entry_time': t.entry_time.isoformat()}
            for t in tr
        ]
    return out


def exact_pass(p):
    st, ds = p['stats'], p['daily']
    return (
        ds['trading_days'] >= 55
        and 3.0 <= ds['avg_trades_per_day'] <= 5.0
        and ds['pct_days_with_3plus_trades'] >= 65.0
        and st['win_rate'] >= 70.0
        and st['expectancy'] >= 3.5
        and st['profit_factor'] >= 2.0
        and ds['avg_net_points_per_day'] >= 15.0
        and ds['pct_profitable_days'] >= 70.0
    )


def main():
    path = _download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot = _parse_spot_frame(path)
    opt = _parse_option_frame(path)
    lo, hi = date(2025,7,1), date(2026,6,30)
    spot = spot[(spot.timestamp.dt.date >= lo) & (spot.timestamp.dt.date <= hi)]
    opt = opt[(opt.day >= lo) & (opt.day <= hi)]
    sbd = {d: tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d,g in spot.groupby(spot.timestamp.dt.date, sort=True)}
    rows = defaultdict(list)
    for r in opt.itertuples(index=False):
        rows[r.day].append(r)

    ie, ism, itm = imp.build_events(sbd, rows)
    pe, psm, ptm = pull.build_events(sbd, rows)
    re, rsm, rtm = rev.build_events(sbd, rows)
    streams = {'IMPULSE': ie, 'PULLBACK': pe, 'REVERSAL': re}
    maps = {'IMPULSE': (ism,itm), 'PULLBACK': (psm,ptm), 'REVERSAL': (rsm,rtm)}
    days = sorted(sbd)

    train = (date(2025,7,1), date(2025,12,31))
    validation = (date(2026,1,1), date(2026,3,31))
    stress = (date(2026,4,1), date(2026,6,30))

    ranked = []
    near = []
    configs = list(grid())
    for cfg in configs:
        st, ds, _ = evaluate(cfg, streams, maps, days, *train)
        raw = (
            st['win_rate']
            + st['profit_factor'] * 3
            + st['expectancy'] * 2
            + ds['avg_trades_per_day'] * 4
            + ds['avg_net_points_per_day']
            + ds['pct_profitable_days'] / 10
        )
        near.append((raw,cfg,st,ds))
        sc = rank_score(st,ds)
        if sc > -1e8:
            ranked.append((sc,cfg,st,ds))
    near.sort(key=lambda x:x[0], reverse=True)
    ranked.sort(key=lambda x:x[0], reverse=True)

    best_training = None
    if near:
        _, c, st, ds = near[0]
        best_training = {'config': asdict(c), 'stats': st, 'daily': ds}

    if not ranked:
        payload = {
            'search_name': 'Shiv Confluence + Failed-Trend Daily Proof',
            'status': 'NO_TRAINING_CANDIDATE',
            'config_count': len(configs),
            'events_built': {k:len(v) for k,v in streams.items()},
            'logic': 'AGREEMENT = same-direction IMPULSE + PULLBACK within frozen window. FAILED_TREND_REVERSAL = REVERSAL opposite a recent IMPULSE/PULLBACK. Sequential real-time only; no end-of-day cherry picking.',
            'best_available_training': best_training,
        }
    else:
        _, cfg, trst, trds = ranked[0]
        val = period(cfg, streams, maps, days, *validation)
        sts = period(cfg, streams, maps, days, *stress)
        combined = period(cfg, streams, maps, days, validation[0], stress[1], include_trades=False)
        proven = exact_pass(val) and exact_pass(sts) and combined['stats']['win_rate'] >= 70 and combined['daily']['avg_net_points_per_day'] >= 15
        payload = {
            'search_name': 'Shiv Confluence + Failed-Trend Daily Proof',
            'status': 'PROVEN_EXACT_TARGET' if proven else 'NO_EXACT_TARGET_PROVEN',
            'config_count': len(configs),
            'events_built': {k:len(v) for k,v in streams.items()},
            'logic': 'AGREEMENT = same-direction IMPULSE + PULLBACK within frozen window. FAILED_TREND_REVERSAL = REVERSAL opposite a recent IMPULSE/PULLBACK. Sequential real-time only; no end-of-day cherry picking.',
            'proof_rule': 'Frozen Jul-Dec 2025 selection. Jan-Mar 2026 validation AND Apr-Jun 2026 stress must each average 3-5 trades/day, >=70% wins, PF>=2, expectancy>=3.5 points/trade, >=15 net option points/day, >=70% profitable days.',
            'chosen_config': asdict(cfg),
            'training': {'stats':trst,'daily':trds},
            'validation': val,
            'stress': sts,
            'combined_oos': combined,
            'best_available_training': best_training,
        }

    Path('strategy_70_confluence.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(payload,indent=2))


if __name__ == '__main__':
    main()
