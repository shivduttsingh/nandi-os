from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from itertools import combinations
from pathlib import Path

from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_shiv_aplus_hf_backtest import HF_REPO, load_hf_frames

TIMEFRAMES = (1, 3, 5, 15)
PRIMARY_INTERVAL = 5
MAX_DTE = 7
COMBOS = tuple(combo for size in (2, 3, 4) for combo in combinations(TIMEFRAMES, size))

# Backtest-only performance cache. This does not alter any market rule; it only
# avoids repeatedly scanning the full option table for the same prior reference.
_original_previous_contract_reference = base.previous_contract_reference
_reference_cache: dict[tuple[object, object, int, str], tuple[float, float] | None] = {}


def _cached_previous_contract_reference(all_opt, day, expiry, strike, side):
    key = (day, expiry, int(strike), str(side))
    if key not in _reference_cache:
        _reference_cache[key] = _original_previous_contract_reference(
            all_opt, day, expiry, int(strike), str(side)
        )
    return _reference_cache[key]


base.previous_contract_reference = _cached_previous_contract_reference


def _name(combo: tuple[int, ...]) -> str:
    return "SHIV_MTF_" + "_".join(str(x) for x in combo)


def _evaluate_combo(
    *,
    system: str,
    combo: tuple[int, ...],
    day_spot,
    spot_by_tf,
    window,
    snapshot,
    now: datetime,
    expiry,
    tracker,
    report,
    primary_regime,
    atm,
    atm_assessment,
    strike_assessment,
    oi_side: str,
    oi_score: float,
) -> None:
    primary = spot_by_tf[PRIMARY_INTERVAL]
    mtf_rows = tuple(base.assess_timeframe(tf, spot_by_tf[tf]) for tf in combo)
    mtf = base.combine_timeframes(mtf_rows)
    candidate_side = base.infer_candidate_side(mtf, atm_assessment, strike_assessment, oi_side)
    preview = base.select_option_strike(snapshot, window, candidate_side)
    premium = preview.selected.premium if preview.selected else None
    selected_strike = preview.selected.strike if preview.selected else None
    persistence, first_seen, first_premium = base.update_tracker(
        tracker, candidate_side, selected_strike, premium, now
    )
    atm_candles = (
        atm.ce_candles if candidate_side == "CE"
        else atm.pe_candles if candidate_side == "PE"
        else tuple()
    )
    core = base.build_shiv_decision(
        interval_minutes=PRIMARY_INTERVAL,
        primary_regime=primary_regime,
        mtf=mtf,
        atm=atm_assessment,
        strike=strike_assessment,
        oi_side=oi_side,
        oi_score=oi_score,
        candidate_side=candidate_side,
        persistence_count=persistence,
        option_spread_pct=None,
        option_strike=atm.strike,
        option_candles=atm_candles,
        similarity=base.SimilarityStats(),
    )
    decision = base.build_safe_v2_decision(
        base=core,
        primary_regime=primary_regime,
        mtf=mtf,
        snapshot=snapshot,
        option_window=window,
        primary_nifty=primary,
        now=now,
        first_seen_at=first_seen,
        first_premium=first_premium,
        expiry=expiry.isoformat(),
    )
    report.eval_counts[system] += 1
    if not decision.actionable or decision.entry_plan.status != "ENTRY READY" or decision.side not in {"CE", "PE"}:
        return
    if tracker.last_signal and now - tracker.last_signal < timedelta(minutes=5):
        return
    tracker.last_signal = now
    mfe, mae, m5, m10, m15, result, entry = base.outcome(day_spot, now, decision.side)
    selected = decision.strike_selection.selected
    report.signals.append(base.SignalResult(
        now,
        system,
        decision.side,
        round(decision.setup_quality, 1),
        round(decision.base.mtf_agreement, 1),
        round(selected.score if selected else 0.0, 1),
        decision.base.persistence_count,
        round(entry, 2),
        round(mfe, 2),
        round(mae, 2),
        round(m5, 2),
        round(m10, 2),
        round(m15, 2),
        result,
    ))


def run(start: date, end: date, cache: Path) -> dict[str, object]:
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    report = base.Report(start, end)
    tested_days: set[date] = set()
    skipped_days: list[str] = []

    days = sorted(day for day in set(spot_df["day"]) if start <= day <= end)
    for day in days:
        day_spot_df = spot_df[spot_df["day"] == day]
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries:
            skipped_days.append(day.isoformat())
            continue
        expiry = future_expiries[0]
        if (expiry - day).days > MAX_DTE:
            skipped_days.append(day.isoformat())
            continue
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            skipped_days.append(day.isoformat())
            continue

        day_spot = [base.candle_from(row) for row in day_spot_df.itertuples(index=False)]
        tested_days.add(day)
        trackers = {_name(combo): base.Tracker() for combo in COMBOS}

        now = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))
        while now <= end_ts:
            completed_1m = tuple(c for c in day_spot if c.timestamp + timedelta(minutes=1) <= now)
            if not completed_1m:
                now += timedelta(minutes=PRIMARY_INTERVAL)
                continue

            spot_by_tf = {
                1: completed_1m,
                3: base.aggregate(day_spot, 3, now),
                5: base.aggregate(day_spot, 5, now),
                15: base.aggregate(day_spot, 15, now),
            }
            primary = spot_by_tf[PRIMARY_INTERVAL]
            if len(primary) < 6:
                now += timedelta(minutes=PRIMARY_INTERVAL)
                continue

            chosen = base.choose_window(day_opt, completed_1m[-1].close, now)
            if chosen is None:
                report.missing_window_evals += 1
                now += timedelta(minutes=PRIMARY_INTERVAL)
                continue
            strikes, chosen_expiry = chosen
            window = base.build_window(day_opt, strikes, chosen_expiry, now)
            if any(not item.ce_candles or not item.pe_candles for item in window):
                report.missing_window_evals += 1
                now += timedelta(minutes=PRIMARY_INTERVAL)
                continue

            # These components are identical across MTF combinations, so compute once.
            primary_regime = base.classify_market_regime(primary)
            atm = next((item for item in window if item.offset == 0), None)
            if atm is None:
                now += timedelta(minutes=PRIMARY_INTERVAL)
                continue
            atm_assessment = base.assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
            strike_assessment = base.assess_strike_window_confirmation(primary, window)
            spot = primary[-1].close
            snapshot = base.build_snapshot(
                opt_df, day_opt, strikes, chosen_expiry, spot, now, False
            )
            oi_side, oi_score = base.oi_engine(snapshot, primary, now)

            for combo in COMBOS:
                system = _name(combo)
                _evaluate_combo(
                    system=system,
                    combo=combo,
                    day_spot=day_spot,
                    spot_by_tf=spot_by_tf,
                    window=window,
                    snapshot=snapshot,
                    now=now,
                    expiry=chosen_expiry,
                    tracker=trackers[system],
                    report=report,
                    primary_regime=primary_regime,
                    atm=atm,
                    atm_assessment=atm_assessment,
                    strike_assessment=strike_assessment,
                    oi_side=oi_side,
                    oi_score=oi_score,
                )
            now += timedelta(minutes=PRIMARY_INTERVAL)

    systems = {_name(combo): report.summary_for(_name(combo)) for combo in COMBOS}
    ranked = sorted(
        (
            {
                "system": name,
                "timeframes": [int(x) for x in name.replace("SHIV_MTF_", "").split("_")],
                "signals": int(summary["signals"]),
                "wins": int(summary["wins"]),
                "losses": int(summary["losses"]),
                "win_rate_pct": float(summary["win_rate_pct"]),
                "mfe_10pt_hit_rate_pct": float(summary["mfe_10pt_hit_rate_pct"]),
                "continuation_5m_pct": float(summary["continuation_5m_pct"]),
            }
            for name, summary in systems.items()
        ),
        key=lambda row: (row["win_rate_pct"], row["signals"]),
        reverse=True,
    )

    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "tested_days": len(tested_days),
        "skipped_days_without_near_expiry": skipped_days,
        "source": HF_REPO,
        "source_license": "CC-BY-NC-4.0",
        "loaded_expiry_files": loaded,
        "primary_execution_timeframe_minutes": PRIMARY_INTERVAL,
        "tested_mtf_combinations": [list(combo) for combo in COMBOS],
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop is a conservative LOSS",
        "methodology": "Current SHIV V2 market/option/OI/entry logic is unchanged. The 5m primary execution timeframe remains fixed; only the set of NIFTY timeframes entering combine_timeframes is varied. Each combination has independent session-local persistence state. Outcome is measured on future 1m NIFTY candles. Common ATM, ATM±2, option-chain and OI evidence is computed once per checkpoint and reused unchanged across combinations.",
        "near_expiry_rule": f"Only sessions whose nearest available expiry is <= {MAX_DTE} calendar days away are included.",
        "systems": systems,
        "ranking": ranked,
        "signals": [
            {
                **row.__dict__,
                "timestamp": row.timestamp.isoformat(),
            }
            for row in report.signals
            if row.system.startswith("SHIV_MTF_")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output", default="shiv_mtf_matrix.json")
    parser.add_argument("--cache", default=".cache/hf-india-options")
    args = parser.parse_args()
    payload = run(args.start, args.end, Path(args.cache))
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
