from __future__ import annotations

import itertools
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.search_strategy_70_walkforward as base
from test1.public_backtest import _download_public_sample, _parse_option_frame, _parse_spot_frame, _row_to_candle


def fast_grid() -> list[base.Candidate]:
    candidates: list[base.Candidate] = []
    exit_pairs = [(4.0, 6.0), (5.0, 7.0), (6.0, 6.0), (8.0, 6.0)]
    for orb, retest, extension, vol, outperf, req_ema, premium, trigger, pair in itertools.product(
        (15, 30),
        (0.20, 0.35, 0.55),
        (0.90, 1.40),
        (0.90, 1.25),
        (0.35, 0.90),
        (False, True),
        (30.0, 50.0),
        (1, 2),
        exit_pairs,
    ):
        candidates.append(base.Candidate(orb, retest, extension, vol, outperf, req_ema, premium, trigger, pair[0], pair[1], 30))
    return candidates


def main() -> None:
    path = _download_public_sample(Path('/tmp/shiv_strategy70/nifty_1y_1min.xlsx'))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    lo, hi = date(2025, 7, 1), date(2026, 6, 30)
    spot_df = spot_df[(spot_df['timestamp'].dt.date >= lo) & (spot_df['timestamp'].dt.date <= hi)]
    opt_df = opt_df[(opt_df['day'] >= lo) & (opt_df['day'] <= hi)]
    spot_by_day = {
        d: tuple(_row_to_candle(r) for r in g.itertuples(index=False))
        for d, g in spot_df.groupby(spot_df['timestamp'].dt.date, sort=True)
    }
    option_rows = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows[row.day].append(row)
    events, series_map, times_map = base.build_events(spot_by_day, option_rows)
    candidates = fast_grid()

    rounds = [
        ((date(2025,7,1), date(2025,9,30)), (date(2025,10,1), date(2025,11,30)), (date(2025,12,1), date(2025,12,31))),
        ((date(2025,7,1), date(2025,11,30)), (date(2025,12,1), date(2026,1,31)), (date(2026,2,1), date(2026,2,28))),
        ((date(2025,7,1), date(2026,1,31)), (date(2026,2,1), date(2026,3,31)), (date(2026,4,1), date(2026,4,30))),
        ((date(2025,7,1), date(2026,3,31)), (date(2026,4,1), date(2026,5,31)), (date(2026,6,1), date(2026,6,30))),
    ]
    results = []
    proven = None
    for round_no, (train_w, proof_w, stress_w) in enumerate(rounds, 1):
        ranked = []
        for candidate in candidates:
            train_stats, _ = base.evaluate(events, candidate, series_map, times_map, *train_w)
            score = base.training_score(train_stats)
            if score > -1e8:
                ranked.append((score, candidate, train_stats))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked:
            results.append({'round': round_no, 'status': 'NO_TRAINING_CANDIDATE'})
            continue
        _, chosen, train_stats = ranked[0]
        proof_stats, proof_trades = base.evaluate(events, chosen, series_map, times_map, *proof_w)
        stress_stats, stress_trades = base.evaluate(events, chosen, series_map, times_map, *stress_w)
        passed = base.proof_pass(proof_stats) and base.stress_pass(stress_stats)
        row = {
            'round': round_no,
            'candidate_frozen_before_proof': True,
            'candidate': asdict(chosen),
            'training_window': [x.isoformat() for x in train_w],
            'proof_window': [x.isoformat() for x in proof_w],
            'stress_window': [x.isoformat() for x in stress_w],
            'training': asdict(train_stats),
            'proof': asdict(proof_stats),
            'stress': asdict(stress_stats),
            'passed': passed,
            'proof_trades': [{**asdict(t), 'day': t.day.isoformat(), 'signal_time': t.signal_time.isoformat(), 'entry_time': t.entry_time.isoformat()} for t in proof_trades],
            'stress_trades': [{**asdict(t), 'day': t.day.isoformat(), 'signal_time': t.signal_time.isoformat(), 'entry_time': t.entry_time.isoformat()} for t in stress_trades],
        }
        results.append(row)
        if passed:
            proven = row
            break
    payload = {
        'search_name': 'Shiv Strategy 70 Fast ORB Walk-Forward',
        'candidate_count': len(candidates),
        'events_built': len(events),
        'proof_rule': 'Frozen candidate must achieve >=14 untouched proof trades, >=70% target wins, positive expectancy and PF>1.15, then >=7 later stress trades with >=60% wins, positive expectancy and PF>1.0.',
        'status': 'PROVEN_70_PLUS' if proven else 'NO_70_PLUS_CANDIDATE_PROVEN',
        'proven_candidate': proven,
        'rounds': results,
    }
    Path('strategy_70_fast.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
