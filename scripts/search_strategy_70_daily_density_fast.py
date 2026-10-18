from __future__ import annotations

import itertools, json, math, sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.search_strategy_70_option_impulse import Candidate, build_events, stats_for
from scripts.search_strategy_70_daily_density import evaluate_dense
from test1.public_backtest import _download_public_sample, _parse_option_frame, _parse_spot_frame, _row_to_candle


def fast_grid():
    exits = [(8.0, 4.0), (10.0, 5.0), (12.0, 5.0)]
    for gap, spotmove, optmove, outperf, vol, pair in itertools.product(
        (0.03, 0.08),
        (0.10, 0.25),
        (0.10, 0.40),
        (0.20, 0.60),
        (0.90, 1.20),
        exits,
    ):
        yield Candidate(gap, spotmove, optmove, outperf, vol, False, 30.0, pair[0], pair[1])


def rank_training(s, ex):
    if ex['trading_days'] < 80 or s.trades < 200:
        return -1e9
    if ex['avg_trades_per_day'] < 2.5 or ex['pct_days_with_3plus_trades'] < 50:
        return -1e9
    if s.win_rate < 50 or s.expectancy <= 0 or s.profit_factor <= 1:
        return -1e9
    p = s.wins / s.trades
    z = 1.0
    lower = (p + z*z/(2*s.trades) - z*math.sqrt((p*(1-p)+z*z/(4*s.trades))/s.trades)) / (1 + z*z/s.trades)
    return lower*100 + min(s.expectancy, 8)*3 + min(ex['avg_net_points_per_day'], 30) + min(s.profit_factor, 4)*4


def summarize_period(trades, trading_days, start, end):
    s = stats_for(trades)
    days = [d for d in trading_days if start <= d <= end]
    daily_net = defaultdict(float)
    daily_count = defaultdict(int)
    for t in trades:
        daily_net[t.day] += t.net_points
        daily_count[t.day] += 1
    n = len(days)
    return asdict(s), {
        'trading_days': n,
        'avg_trades_per_day': round(len(trades)/n, 2) if n else 0.0,
        'avg_net_points_per_day': round(sum(daily_net.values())/n, 2) if n else 0.0,
        'pct_days_with_3plus_trades': round(100*sum(daily_count[d] >= 3 for d in days)/n, 2) if n else 0.0,
        'pct_profitable_days': round(100*sum(daily_net[d] > 0 for d in days)/n, 2) if n else 0.0,
    }


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

    train_start, train_end = date(2025,7,1), date(2025,12,31)
    val_start, val_end = date(2026,1,1), date(2026,3,31)
    stress_start, stress_end = date(2026,4,1), date(2026,6,30)

    ranked = []
    candidates = list(fast_grid())
    for c in candidates:
        s, _, ex = evaluate_dense(events, c, series_map, times_map, trading_days, train_start, train_end)
        score = rank_training(s, ex)
        if score > -1e8:
            ranked.append((score, c, s, ex))
    ranked.sort(key=lambda x: x[0], reverse=True)

    if not ranked:
        payload = {
            'search_name':'Shiv Daily Density Fast Proof',
            'candidate_count':len(candidates),
            'events_built':len(events),
            'status':'NO_TRAINING_CANDIDATE',
            'proof_rule':'Freeze on Jul-Dec 2025; Jan-Mar 2026 must deliver 3-5 trades/day, >=70% wins and >=15 net points/day; Apr-Jun stress must remain robust.',
        }
    else:
        _, chosen, train_stats, train_ex = ranked[0]
        val_stats, val_trades, val_ex = evaluate_dense(events, chosen, series_map, times_map, trading_days, val_start, val_end)
        stress_stats, stress_trades, stress_ex = evaluate_dense(events, chosen, series_map, times_map, trading_days, stress_start, stress_end)
        val_pass = (
            val_stats.trades >= 150 and 3.0 <= val_ex['avg_trades_per_day'] <= 5.0
            and val_ex['pct_days_with_3plus_trades'] >= 65 and val_stats.win_rate >= 70
            and val_stats.expectancy >= 3.5 and val_stats.profit_factor >= 2.0
            and val_ex['avg_net_points_per_day'] >= 15 and val_ex['pct_profitable_days'] >= 70
        )
        stress_pass = (
            stress_stats.trades >= 120 and stress_ex['avg_trades_per_day'] >= 2.5
            and stress_stats.win_rate >= 65 and stress_stats.expectancy >= 2.5
            and stress_stats.profit_factor >= 1.5 and stress_ex['avg_net_points_per_day'] >= 10
        )
        payload = {
            'search_name':'Shiv Daily Density Fast Proof',
            'candidate_count':len(candidates),
            'events_built':len(events),
            'status':'PROVEN_DAILY_70_PLUS' if val_pass and stress_pass else 'NO_DAILY_70_PLUS_PROVEN',
            'method':'Candidate selected only on Jul-Dec 2025, then frozen. Jan-Mar 2026 validation and Apr-Jun 2026 stress are untouched by selection.',
            'proof_rule':'Validation: >=150 trades, 3-5 trades/day, >=70% wins, expectancy>=3.5 after friction, PF>=2, >=15 net pts/day, >=70% profitable days. Stress: >=65% wins, expectancy>=2.5, PF>=1.5, >=10 pts/day.',
            'execution_note':'ATM option next-minute buy-stop; 0.20 entry slippage + 0.50 friction; max 5 trades/day; 8-minute cooldown.',
            'chosen_candidate':asdict(chosen),
            'training':{'stats':asdict(train_stats),'daily':train_ex},
            'validation':{'stats':asdict(val_stats),'daily':val_ex,'passed':val_pass},
            'stress':{'stats':asdict(stress_stats),'daily':stress_ex,'passed':stress_pass},
        }
    Path('strategy_70_daily_density_fast.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
