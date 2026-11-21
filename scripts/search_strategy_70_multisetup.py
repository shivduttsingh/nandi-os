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
    target_points: float
    stop_points: float
    cooldown_minutes: int
    max_same_family: int
    max_trades_day: int = 5


@dataclass(frozen=True)
class Wrapped:
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


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def impulse_pass_score(e, level: int):
    rules = (
        (0.03, 0.10, 0.10, 0.20, 0.90, 30.0),
        (0.08, 0.20, 0.30, 0.50, 1.10, 30.0),
        (0.12, 0.35, 0.50, 0.80, 1.20, 50.0),
    )
    g, sm, om, op, vr, prem = rules[level]
    ok = (
        e.spot_trend_gap_atr >= g and e.spot_move_atr >= sm
        and e.option_move_pct >= om and e.option_outperformance_pct >= op
        and e.option_volume_ratio >= vr and e.option_premium >= prem
        and e.option_oi_change_pct >= -15.0
    )
    if not ok:
        return None
    score = (
        clamp01(e.spot_trend_gap_atr / 0.18)
        + clamp01(e.spot_move_atr / 0.65)
        + clamp01(e.option_move_pct / 1.5)
        + clamp01(e.option_outperformance_pct / 2.0)
        + clamp01(e.option_volume_ratio / 1.8)
        + (1.0 if e.option_ema_aligned else 0.35)
    ) / 6.0
    return score


def pullback_pass_score(e, level: int):
    rules = (
        (0.04, 0.80, 0.50, 3.5, 0.90, 0.30, 30.0),
        (0.08, 0.55, 0.80, 3.0, 1.10, 0.60, 40.0),
        (0.12, 0.35, 1.00, 2.5, 1.20, 0.90, 50.0),
    )
    gap, pb, mini, maxi, vr, outp, prem = rules[level]
    ok = (
        e.trend_gap_atr >= gap and e.pullback_depth_atr <= pb
        and mini <= e.impulse_atr <= maxi and e.option_volume_ratio >= vr
        and e.option_outperformance >= outp and e.option_premium >= prem
        and e.option_oi_change >= -15.0
    )
    if not ok:
        return None
    score = (
        clamp01(e.trend_gap_atr / 0.18)
        + clamp01((pb - min(e.pullback_depth_atr, pb)) / max(pb, 1e-6))
        + clamp01(e.impulse_atr / 2.5)
        + clamp01(e.option_outperformance / 2.0)
        + clamp01(e.option_volume_ratio / 1.8)
        + (1.0 if e.option_ema_aligned else 0.35)
    ) / 6.0
    return score


def reversal_pass_score(e, level: int):
    rules = (
        (0.80, 0.20, 0.35, 0.40, 0.10, 0.30, 0.90, 30.0),
        (1.00, 0.25, 0.40, 0.50, 0.20, 0.50, 1.10, 40.0),
        (1.20, 0.30, 0.50, 0.60, 0.30, 0.70, 1.30, 50.0),
    )
    ia, wick, body, ext, om, op, vr, prem = rules[level]
    ok = (
        e.impulse_atr >= ia and e.wick_ratio >= wick and e.body_ratio >= body
        and e.five_extension_atr >= ext and e.option_move_1m_pct >= om
        and e.option_outperformance_1m_pct >= op and e.option_volume_ratio >= vr
        and e.option_premium >= prem
    )
    if not ok:
        return None
    score = (
        clamp01(e.impulse_atr / 1.8)
        + clamp01(e.wick_ratio / 0.55)
        + clamp01(e.body_ratio / 0.75)
        + clamp01(e.five_extension_atr / 1.0)
        + clamp01(e.option_outperformance_1m_pct / 1.8)
        + clamp01(e.option_volume_ratio / 1.8)
    ) / 6.0
    return score


def generic_stats(trades):
    trades = tuple(trades)
    n = len(trades)
    wins = sum(t.outcome == 'WIN' for t in trades)
    losses = sum(t.outcome == 'LOSS' for t in trades)
    timeouts = n - wins - losses
    net = sum(t.net_points for t in trades)
    gains = sum(max(0.0, t.net_points) for t in trades)
    loss_value = abs(sum(min(0.0, t.net_points) for t in trades))
    pf = gains / loss_value if loss_value else (gains if gains else 0.0)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t.net_points
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


def daily_stats(trades, trading_days, start_day, end_day):
    days = [d for d in trading_days if start_day <= d <= end_day]
    count = defaultdict(int)
    net = defaultdict(float)
    fam = defaultdict(lambda: defaultdict(int))
    for t in trades:
        count[t.day] += 1
        net[t.day] += t.net_points
        fam[t.day][t.family] += 1
    n = len(days)
    return {
        'trading_days': n,
        'avg_trades_per_day': round(len(trades) / n, 2) if n else 0.0,
        'avg_net_points_per_day': round(sum(net.values()) / n, 2) if n else 0.0,
        'pct_days_with_3plus_trades': round(100 * sum(count[d] >= 3 for d in days) / n, 2) if n else 0.0,
        'pct_profitable_days': round(100 * sum(net[d] > 0 for d in days) / n, 2) if n else 0.0,
        'pct_days_15plus_points': round(100 * sum(net[d] >= 15 for d in days) / n, 2) if n else 0.0,
        'max_trades_day': max((count[d] for d in days), default=0),
        'family_totals': {k: sum(fam[d].get(k, 0) for d in days) for k in ('IMPULSE','PULLBACK','REVERSAL')},
    }


def simulate_wrapped(w: Wrapped, cfg: Config, maps):
    target, stop = cfg.target_points, cfg.stop_points
    if w.family == 'IMPULSE':
        c = imp.Candidate(0, 0, 0, 0, 0, False, 0, target, stop)
        s, ts = maps['IMPULSE'][0].get(w.event.series_key), maps['IMPULSE'][1].get(w.event.series_key)
        tr = imp.simulate(w.event, c, s, ts) if s and ts else None
    elif w.family == 'PULLBACK':
        c = pull.Candidate(0, 999, 0, 999, 0, 0, False, 0, 2, target, stop, 25)
        s, ts = maps['PULLBACK'][0].get(w.event.series_key), maps['PULLBACK'][1].get(w.event.series_key)
        tr = pull.simulate(w.event, c, s, ts) if s and ts else None
    else:
        c = rev.Candidate(0, 0, 0, 0, 0, 0, 0, 0, target, stop)
        s, ts = maps['REVERSAL'][0].get(w.event.series_key), maps['REVERSAL'][1].get(w.event.series_key)
        tr = rev.simulate(w.event, c, s, ts) if s and ts else None
    if tr is None:
        return None
    return Trade(tr.day, w.family, tr.signal_time, tr.entry_time, tr.direction, tr.strike, tr.outcome, tr.net_points)


def evaluate(cfg, all_events, maps, trading_days, start_day, end_day):
    by_day = defaultdict(list)
    for family, events in all_events.items():
        for e in events:
            if not (start_day <= e.day <= end_day):
                continue
            if family == 'IMPULSE':
                score = impulse_pass_score(e, cfg.impulse_level)
            elif family == 'PULLBACK':
                score = pullback_pass_score(e, cfg.pullback_level)
            else:
                score = reversal_pass_score(e, cfg.reversal_level)
            if score is not None and score >= cfg.min_score:
                by_day[e.day].append(Wrapped(family, e, score))

    trades = []
    for d in [x for x in trading_days if start_day <= x <= end_day]:
        events = sorted(by_day.get(d, []), key=lambda w: w.event.signal_time)
        last_entry = None
        family_count = defaultdict(int)
        seen_keys = set()
        for w in events:
            if sum(family_count.values()) >= cfg.max_trades_day:
                break
            key = (w.event.signal_time, w.event.direction, w.event.strike)
            if key in seen_keys:
                continue
            if family_count[w.family] >= cfg.max_same_family:
                continue
            if last_entry is not None and (w.event.signal_time - last_entry).total_seconds() < cfg.cooldown_minutes * 60:
                continue
            tr = simulate_wrapped(w, cfg, maps)
            if tr is None:
                continue
            trades.append(tr)
            family_count[w.family] += 1
            last_entry = tr.entry_time
            seen_keys.add(key)
    return generic_stats(trades), daily_stats(trades, trading_days, start_day, end_day), tuple(trades)


def config_grid():
    exits = ((6.0,4.0),(8.0,4.0),(8.0,5.0),(10.0,5.0))
    for il, pl, rl, score, pair, cooldown, quota in itertools.product(
        (0,1), (0,1), (0,1), (0.45,0.55,0.65), exits, (5,8), (2,3)
    ):
        yield Config(il, pl, rl, score, pair[0], pair[1], cooldown, quota)


def rank_score(st, ds):
    if ds['trading_days'] == 0:
        return -1e9
    if st['trades'] < ds['trading_days'] * 2.3:
        return -1e9
    if ds['avg_trades_per_day'] < 2.3 or ds['pct_days_with_3plus_trades'] < 45:
        return -1e9
    if st['expectancy'] <= 0 or st['profit_factor'] <= 1.0:
        return -1e9
    p = st['wins'] / st['trades'] if st['trades'] else 0
    z = 1.0
    n = st['trades']
    lower = (p + z*z/(2*n) - z*math.sqrt((p*(1-p)+z*z/(4*n))/n)) / (1 + z*z/n)
    return (
        lower * 100
        + min(st['profit_factor'], 4) * 4
        + min(st['expectancy'], 8) * 2
        + min(ds['avg_net_points_per_day'], 25)
        + ds['pct_profitable_days'] / 10
        - st['max_drawdown'] / 150
    )


def period_payload(cfg, all_events, maps, days, start, end):
    st, ds, tr = evaluate(cfg, all_events, maps, days, start, end)
    monthly = {}
    for y, m in sorted({(d.year, d.month) for d in days if start <= d <= end}):
        ms = date(y,m,1)
        me = date(y,12,31) if m == 12 else date(y,m+1,1)
        me = min(end, me if m == 12 else me)
        if m != 12:
            from datetime import timedelta
            me = me - timedelta(days=1)
        mst, mds, _ = evaluate(cfg, all_events, maps, days, max(start,ms), min(end,me))
        monthly[f'{y:04d}-{m:02d}'] = {'stats': mst, 'daily': mds}
    return {'stats': st, 'daily': ds, 'monthly': monthly, 'trades': [
        {**asdict(t), 'day': t.day.isoformat(), 'signal_time': t.signal_time.isoformat(), 'entry_time': t.entry_time.isoformat()}
        for t in tr
    ]}


def passes_period(p):
    st, ds = p['stats'], p['daily']
    return (
        ds['trading_days'] >= 55
        and 3.0 <= ds['avg_trades_per_day'] <= 5.0
        and ds['pct_days_with_3plus_trades'] >= 65
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
    all_events = {'IMPULSE': ie, 'PULLBACK': pe, 'REVERSAL': re}
    maps = {'IMPULSE': (ism,itm), 'PULLBACK': (psm,ptm), 'REVERSAL': (rsm,rtm)}
    trading_days = sorted(sbd)

    train = (date(2025,7,1), date(2025,12,31))
    validation = (date(2026,1,1), date(2026,3,31))
    stress = (date(2026,4,1), date(2026,6,30))
    ranked = []
    near = []
    for cfg in config_grid():
        st, ds, _ = evaluate(cfg, all_events, maps, trading_days, *train)
        raw = st['win_rate'] + ds['avg_net_points_per_day'] + ds['avg_trades_per_day']*3 + st['profit_factor']*2
        near.append((raw, cfg, st, ds))
        sc = rank_score(st, ds)
        if sc > -1e8:
            ranked.append((sc, cfg, st, ds))
    ranked.sort(key=lambda x:x[0], reverse=True)
    near.sort(key=lambda x:x[0], reverse=True)

    best_available = None
    if near:
        _, nc, ns, nd = near[0]
        best_available = {'config': asdict(nc), 'training': ns, 'training_daily': nd}

    if not ranked:
        payload = {
            'search_name': 'Shiv Multi-Setup Daily Selector 70 Proof',
            'status': 'NO_TRAINING_CANDIDATE',
            'config_count': sum(1 for _ in config_grid()),
            'events_built': {k: len(v) for k,v in all_events.items()},
            'families': list(all_events),
            'selection_rule': 'Sequential real-time threshold selection; no end-of-day cherry picking. Max 5 trades/day, cooldown and family quota frozen in training.',
            'best_available_training': best_available,
        }
    else:
        _, cfg, trst, trds = ranked[0]
        val = period_payload(cfg, all_events, maps, trading_days, *validation)
        sts = period_payload(cfg, all_events, maps, trading_days, *stress)
        combined = period_payload(cfg, all_events, maps, trading_days, validation[0], stress[1])
        proven = passes_period(val) and passes_period(sts) and combined['stats']['win_rate'] >= 70 and combined['daily']['avg_net_points_per_day'] >= 15
        payload = {
            'search_name': 'Shiv Multi-Setup Daily Selector 70 Proof',
            'status': 'PROVEN_EXACT_TARGET' if proven else 'NO_EXACT_TARGET_PROVEN',
            'config_count': sum(1 for _ in config_grid()),
            'events_built': {k: len(v) for k,v in all_events.items()},
            'families': list(all_events),
            'proof_rule': 'Frozen Jul-Dec 2025 selection; Jan-Mar 2026 validation AND Apr-Jun 2026 stress must each average 3-5 trades/day, >=70% wins, PF>=2, expectancy>=3.5 points/trade, >=15 net option points/day, >=70% profitable days.',
            'selection_rule': 'Sequential real-time threshold selection; no end-of-day cherry picking. Max 5 trades/day, cooldown and family quota frozen in training.',
            'chosen_config': asdict(cfg),
            'training': {'stats': trst, 'daily': trds},
            'validation': val,
            'stress': sts,
            'combined_oos': combined,
            'best_available_training': best_available,
        }
    Path('strategy_70_multisetup.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
