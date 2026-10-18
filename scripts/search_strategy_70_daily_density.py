from __future__ import annotations

import itertools, json, math, sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.search_strategy_70_option_impulse import Candidate, build_events, simulate, stats_for
from test1.public_backtest import _download_public_sample, _parse_option_frame, _parse_spot_frame, _row_to_candle


def month_end(y: int, m: int) -> date:
    return date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)


def dense_grid():
    exits = [(8.0, 4.0), (10.0, 5.0), (12.0, 5.0)]
    for gap, spotmove, optmove, outperf, vol, req, premium, pair in itertools.product(
        (0.03, 0.08),
        (0.10, 0.25),
        (0.10, 0.40),
        (0.20, 0.60),
        (0.90, 1.20),
        (False, True),
        (30.0, 50.0),
        exits,
    ):
        yield Candidate(gap, spotmove, optmove, outperf, vol, req, premium, pair[0], pair[1])


def evaluate_dense(events, candidate, series_map, times_map, trading_days, start_day, end_day, max_trades=5, cooldown_minutes=8):
    by_day = defaultdict(list)
    for e in events:
        if start_day <= e.day <= end_day:
            if (
                e.spot_trend_gap_atr >= candidate.min_spot_gap_atr
                and e.spot_move_atr >= candidate.min_spot_move_atr
                and e.option_move_pct >= candidate.min_option_move_pct
                and e.option_outperformance_pct >= candidate.min_outperformance_pct
                and e.option_volume_ratio >= candidate.min_volume_ratio
                and (not candidate.require_option_ema or e.option_ema_aligned)
                and e.option_premium >= candidate.min_premium
                and e.option_oi_change_pct >= -15.0
            ):
                by_day[e.day].append(e)

    trades = []
    daily_counts = defaultdict(int)
    daily_net = defaultdict(float)
    for d in [x for x in trading_days if start_day <= x <= end_day]:
        last_entry = None
        for e in by_day.get(d, []):
            if daily_counts[d] >= max_trades:
                break
            if last_entry is not None and (e.signal_time - last_entry).total_seconds() < cooldown_minutes * 60:
                continue
            series = series_map.get(e.series_key)
            times = times_map.get(e.series_key)
            if not series or not times:
                continue
            tr = simulate(e, candidate, series, times)
            if tr is None:
                continue
            trades.append(tr)
            daily_counts[d] += 1
            daily_net[d] += tr.net_points
            last_entry = tr.entry_time

    s = stats_for(trades)
    days = [x for x in trading_days if start_day <= x <= end_day]
    n_days = len(days)
    avg_trades_day = len(trades) / n_days if n_days else 0.0
    avg_net_day = sum(daily_net.values()) / n_days if n_days else 0.0
    days_3plus = sum(1 for d in days if daily_counts[d] >= 3)
    pct_days_3plus = 100.0 * days_3plus / n_days if n_days else 0.0
    profitable_days = sum(1 for d in days if daily_net[d] > 0)
    pct_profitable_days = 100.0 * profitable_days / n_days if n_days else 0.0
    return s, tuple(trades), {
        'trading_days': n_days,
        'avg_trades_per_day': round(avg_trades_day, 2),
        'avg_net_points_per_day': round(avg_net_day, 2),
        'pct_days_with_3plus_trades': round(pct_days_3plus, 2),
        'pct_profitable_days': round(pct_profitable_days, 2),
        'max_trades_per_day': max(daily_counts.values()) if daily_counts else 0,
    }


def rank_score(stats, extra):
    if stats.trades < max(45, extra['trading_days'] * 2):
        return -1e9
    if extra['avg_trades_per_day'] < 2.5 or extra['pct_days_with_3plus_trades'] < 55:
        return -1e9
    if stats.expectancy <= 0 or stats.profit_factor <= 1:
        return -1e9
    p = stats.wins / stats.trades
    z = 1.0
    lower = (p + z*z/(2*stats.trades) - z*math.sqrt((p*(1-p)+z*z/(4*stats.trades))/stats.trades)) / (1 + z*z/stats.trades)
    return lower*100 + min(stats.expectancy, 8)*2 + min(stats.profit_factor, 4)*4 + min(extra['avg_net_points_per_day'], 25) - stats.max_drawdown/120


def main():
    path = _download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    lo, hi = date(2025, 7, 1), date(2026, 6, 30)
    spot_df = spot_df[(spot_df['timestamp'].dt.date >= lo) & (spot_df['timestamp'].dt.date <= hi)]
    opt_df = opt_df[(opt_df['day'] >= lo) & (opt_df['day'] <= hi)]
    spot_by_day = {d: tuple(_row_to_candle(r) for r in g.itertuples(index=False)) for d, g in spot_df.groupby(spot_df['timestamp'].dt.date, sort=True)}
    option_rows = defaultdict(list)
    for r in opt_df.itertuples(index=False):
        option_rows[r.day].append(r)
    events, series_map, times_map = build_events(spot_by_day, option_rows)
    trading_days = sorted(spot_by_day)
    candidates = list(dense_grid())

    folds = []
    all_oos = []
    all_oos_days = []
    test_months = [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    for y, m in test_months:
        ts, te = date(y, m, 1), month_end(y, m)
        train_end = ts - timedelta(days=1)
        ranked = []
        for c in candidates:
            st, _, ex = evaluate_dense(events, c, series_map, times_map, trading_days, lo, train_end)
            sc = rank_score(st, ex)
            if sc > -1e8:
                ranked.append((sc, c, st, ex))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked:
            folds.append({'month': ts.strftime('%Y-%m'), 'status': 'NO_TRAINING_CANDIDATE'})
            continue
        _, c, trst, trex = ranked[0]
        tst, ttr, tex = evaluate_dense(events, c, series_map, times_map, trading_days, ts, te)
        all_oos.extend(ttr)
        all_oos_days.extend([d for d in trading_days if ts <= d <= te])
        folds.append({
            'month': ts.strftime('%Y-%m'),
            'candidate_frozen_before_month': True,
            'candidate': asdict(c),
            'training': asdict(trst),
            'training_daily': trex,
            'test': asdict(tst),
            'test_daily': tex,
        })

    agg = stats_for(all_oos)
    daily_net = defaultdict(float)
    daily_count = defaultdict(int)
    for t in all_oos:
        daily_net[t.day] += t.net_points
        daily_count[t.day] += 1
    unique_days = sorted(set(all_oos_days))
    n_days = len(unique_days)
    avg_tpd = len(all_oos) / n_days if n_days else 0.0
    avg_net_day = sum(daily_net.values()) / n_days if n_days else 0.0
    pct_3plus = 100 * sum(daily_count[d] >= 3 for d in unique_days) / n_days if n_days else 0.0
    pct_prof_days = 100 * sum(daily_net[d] > 0 for d in unique_days) / n_days if n_days else 0.0

    proven = (
        n_days >= 50
        and agg.trades >= 150
        and 3.0 <= avg_tpd <= 5.0
        and pct_3plus >= 65.0
        and agg.win_rate >= 70.0
        and agg.expectancy >= 3.5
        and agg.profit_factor >= 2.0
        and avg_net_day >= 15.0
        and pct_prof_days >= 70.0
    )
    payload = {
        'search_name': 'Shiv Daily Density 70 Proof',
        'candidate_count': len(candidates),
        'events_built': len(events),
        'status': 'PROVEN_DAILY_70_PLUS' if proven else 'NO_DAILY_70_PLUS_PROVEN',
        'proof_rule': '>=50 OOS trading days, >=150 trades, 3-5 trades/day average, >=65% days with 3+ trades, >=70% wins, expectancy >=3.5 option points/trade after friction, PF>=2.0, >=15 net option points/day average, >=70% profitable days.',
        'execution_note': 'ATM option buy-stop above completed signal candle, next minute only, 0.20 entry slippage + 0.50 friction, max 5 trades/day, 8-minute cooldown.',
        'aggregate_oos': asdict(agg),
        'oos_daily': {
            'trading_days': n_days,
            'avg_trades_per_day': round(avg_tpd, 2),
            'avg_net_points_per_day': round(avg_net_day, 2),
            'pct_days_with_3plus_trades': round(pct_3plus, 2),
            'pct_profitable_days': round(pct_prof_days, 2),
        },
        'folds': folds,
    }
    Path('strategy_70_daily_density.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
