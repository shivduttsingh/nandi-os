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

import scripts.search_strategy_70_trend as base
from test1.public_backtest import _download_public_sample, _parse_option_frame, _parse_spot_frame, _row_to_candle


def fast_grid() -> list[base.Candidate]:
    result = []
    exit_pairs = [(4.0,6.0),(5.0,7.0),(6.0,6.0),(8.0,6.0)]
    for trend,pull,minimp,maximp,vol,outperf,req,premium,trigger,pair in itertools.product(
        (0.08,0.16),
        (0.35,0.70),
        (0.6,1.0),
        (2.2,3.2),
        (0.9,1.25),
        (0.35,0.90),
        (False,True),
        (30.0,50.0),
        (1,2),
        exit_pairs,
    ):
        result.append(base.Candidate(trend,pull,minimp,maximp,vol,outperf,req,premium,trigger,pair[0],pair[1],30))
    return result


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
    events,series_map,times_map=base.build_events(spot_by_day,option_rows)
    candidates=fast_grid()
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
            s,_=base.evaluate(events,c,series_map,times_map,*train_w)
            score=base.rank_score(s)
            if score>-1e8: ranked.append((score,c,s))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if not ranked:
            output.append({'round':i,'status':'NO_TRAINING_CANDIDATE'})
            continue
        _,c,train_s=ranked[0]
        proof_s,proof_trades=base.evaluate(events,c,series_map,times_map,*proof_w)
        stress_s=base.Stats(0,0,0,0,0,0,0,0,0,0)
        stress_trades=()
        stress_ok=True
        if stress_w:
            stress_s,stress_trades=base.evaluate(events,c,series_map,times_map,*stress_w)
            stress_ok=base.stress(stress_s)
        passed=base.proof(proof_s) and stress_ok
        row={'round':i,'candidate_frozen_before_proof':True,'candidate':asdict(c),'training_window':[x.isoformat() for x in train_w],'proof_window':[x.isoformat() for x in proof_w],'stress_window':[x.isoformat() for x in stress_w] if stress_w else None,'training':asdict(train_s),'proof':asdict(proof_s),'stress':asdict(stress_s),'passed':passed,'proof_trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in proof_trades],'stress_trades':[{**asdict(t),'day':t.day.isoformat(),'signal_time':t.signal_time.isoformat(),'entry_time':t.entry_time.isoformat()} for t in stress_trades]}
        output.append(row)
        if passed:
            proven=row
            break
    payload={'search_name':'Shiv Trend-Pullback Strategy 70 Fast Search','candidate_count':len(candidates),'events_built':len(events),'proof_rule':'>=14 untouched proof trades, >=70% target wins, positive expectancy and PF>1.15; then later stress >=7 trades, >=60% wins, positive expectancy and PF>1.0.','status':'PROVEN_70_PLUS' if proven else 'NO_70_PLUS_CANDIDATE_PROVEN','proven_candidate':proven,'rounds':output}
    Path('strategy_70_trend_fast.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(payload,indent=2))


if __name__=='__main__': main()
