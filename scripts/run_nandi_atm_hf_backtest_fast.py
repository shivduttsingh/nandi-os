from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.strike_window_strategy import StrikeWindowSignal, assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_nandi_atm_hf_backtest import (
    PRIMARY_INTERVAL,
    SYSTEM_ATM,
    SYSTEM_WINDOW,
    ClusterTracker,
    Report,
    _record_if_new,
)
from scripts.run_shiv_aplus_hf_backtest import load_hf_frames

MAX_DTE = 7


def _completed(series, now: datetime):
    return tuple(c for c in series if c.timestamp + timedelta(minutes=PRIMARY_INTERVAL) <= now)


def run(start: date, end: date, cache: Path):
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    report = Report(start, end)
    skipped_far_expiry: list[str] = []
    days = sorted(day for day in set(spot_df["day"]) if start <= day <= end)

    for day in days:
        day_spot_df = spot_df[spot_df["day"] == day]
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries:
            continue
        expiry = future_expiries[0]
        if (expiry - day).days > MAX_DTE:
            skipped_far_expiry.append(day.isoformat())
            continue
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            continue

        day_spot = [base.candle_from(row) for row in day_spot_df.itertuples(index=False)]
        full_now = datetime.combine(day, time(15, 31))
        spot_5m_full = base.aggregate(day_spot, PRIMARY_INTERVAL, full_now)

        option_5m = {}
        first_seen = {}
        for (strike, side), group in day_opt.groupby(["strike", "side"], sort=False):
            raw = [base.candle_from(row) for row in group.sort_values("timestamp").itertuples(index=False)]
            if not raw:
                continue
            key = (int(strike), str(side))
            first_seen[key] = raw[0].timestamp
            option_5m[key] = base.aggregate(raw, PRIMARY_INTERVAL, full_now)

        common_strikes = sorted(
            strike for strike in {key[0] for key in option_5m}
            if (strike, "CE") in option_5m and (strike, "PE") in option_5m
        )
        if len(common_strikes) < 5:
            continue

        report.tested_days.add(day)
        atm_tracker = ClusterTracker()
        window_tracker = ClusterTracker()
        now = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))

        while now <= end_ts:
            primary = _completed(spot_5m_full, now)
            if len(primary) < 4:
                now += timedelta(minutes=5)
                continue

            visible = [
                strike for strike in common_strikes
                if first_seen.get((strike, "CE"), now + timedelta(days=1)) < now
                and first_seen.get((strike, "PE"), now + timedelta(days=1)) < now
            ]
            if len(visible) < 5:
                report.missing_window_evaluations += 1
                atm_tracker.active_side = ""
                window_tracker.active_side = ""
                now += timedelta(minutes=5)
                continue

            atm_i = min(range(len(visible)), key=lambda i: abs(visible[i] - primary[-1].close))
            if atm_i < 2 or atm_i + 2 >= len(visible):
                report.missing_window_evaluations += 1
                atm_tracker.active_side = ""
                window_tracker.active_side = ""
                now += timedelta(minutes=5)
                continue
            strikes = visible[atm_i - 2:atm_i + 3]
            window = tuple(
                OptionStrikeCandles(
                    strike=float(strike),
                    expiry=expiry.isoformat(),
                    offset=offset,
                    ce_candles=_completed(option_5m[(strike, "CE")], now),
                    pe_candles=_completed(option_5m[(strike, "PE")], now),
                )
                for offset, strike in enumerate(strikes, start=-2)
            )
            atm = window[2]
            if not atm.ce_candles or not atm.pe_candles:
                report.missing_window_evaluations += 1
                atm_tracker.active_side = ""
                window_tracker.active_side = ""
                now += timedelta(minutes=5)
                continue

            report.evaluations[SYSTEM_ATM] += 1
            atm_assessment = assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
            atm_side = (
                "CE" if atm_assessment.signal == ATMConfirmationSignal.CONFIRM_CE
                else "PE" if atm_assessment.signal == ATMConfirmationSignal.CONFIRM_PE
                else ""
            )
            _record_if_new(
                system=SYSTEM_ATM, side=atm_side, score=atm_assessment.agreement_score,
                tracker=atm_tracker, report=report, day_spot=day_spot, now=now,
            )

            if all(item.ce_candles and item.pe_candles for item in window):
                report.evaluations[SYSTEM_WINDOW] += 1
                assessment = assess_strike_window_confirmation(primary, window)
                side = (
                    "CE" if assessment.signal == StrikeWindowSignal.CONFIRM_CE
                    else "PE" if assessment.signal == StrikeWindowSignal.CONFIRM_PE
                    else ""
                )
                _record_if_new(
                    system=SYSTEM_WINDOW, side=side, score=assessment.agreement_score,
                    tracker=window_tracker, report=report, day_spot=day_spot, now=now,
                )
            else:
                report.missing_window_evaluations += 1
                window_tracker.active_side = ""

            now += timedelta(minutes=5)
    return report, loaded, skipped_far_expiry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output", default="nandi_atm_hf_two_month_results.json")
    parser.add_argument("--cache", default=".cache/hf-india-options")
    args = parser.parse_args()
    report, loaded, skipped = run(args.start, args.end, Path(args.cache))
    payload = report.payload(loaded)
    payload["near_expiry_rule"] = f"Only sessions with the nearest available expiry <= {MAX_DTE} calendar days away are included."
    payload["skipped_days_without_near_expiry"] = skipped
    payload["runner_note"] = "Fast runner pre-aggregates historical candles only; it calls the same current Nandi strategy functions and thresholds."
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
