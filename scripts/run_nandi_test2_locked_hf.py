from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from nandi_oi.models import OptionStrikeCandles
from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.strike_window_strategy import StrikeWindowSignal, assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_shiv_aplus_hf_backtest import load_hf_frames

PRIMARY_INTERVAL = 5
ATM_MIN_SCORE = 95.0
WINDOW_MIN_SCORE = 80.0
MAX_DAYS_TO_EXPIRY = 7
SYSTEM = "NANDI_TEST2_LOCKED_CE_CONSENSUS"


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    atm_score: float
    window_score: float
    entry_nifty: float
    mfe_points: float
    mae_points: float
    move_5m: float
    move_10m: float
    move_15m: float
    target_10_stop_5: str


def _completed(series, now: datetime):
    return tuple(c for c in series if c.timestamp + timedelta(minutes=PRIMARY_INTERVAL) <= now)


def _outcome(day_spot, now: datetime):
    return base.outcome(day_spot, now, "CE")


def run(start: date, end: date, cache: Path):
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    signals: list[Signal] = []
    tested_days = set()
    skipped_days = []
    evaluations = 0
    qualifying_checkpoints = 0
    active = False

    for day in sorted(day for day in set(spot_df["day"]) if start <= day <= end):
        day_spot_df = spot_df[spot_df["day"] == day]
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries or (future_expiries[0] - day).days > MAX_DAYS_TO_EXPIRY:
            skipped_days.append(day.isoformat())
            continue
        expiry = future_expiries[0]
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            skipped_days.append(day.isoformat())
            continue

        day_spot = [base.candle_from(row) for row in day_spot_df.itertuples(index=False)]
        full_now = datetime.combine(day, time(15, 31))
        spot_5m = base.aggregate(day_spot, PRIMARY_INTERVAL, full_now)
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
            strike for strike in {k[0] for k in option_5m}
            if (strike, "CE") in option_5m and (strike, "PE") in option_5m
        )
        if len(common_strikes) < 5:
            skipped_days.append(day.isoformat())
            continue

        tested_days.add(day)
        active = False
        now = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))
        while now <= end_ts:
            primary = _completed(spot_5m, now)
            if len(primary) < 5:
                now += timedelta(minutes=5)
                continue
            visible = [
                strike for strike in common_strikes
                if first_seen.get((strike, "CE"), now + timedelta(days=1)) < now
                and first_seen.get((strike, "PE"), now + timedelta(days=1)) < now
            ]
            if len(visible) < 5:
                active = False
                now += timedelta(minutes=5)
                continue
            atm_i = min(range(len(visible)), key=lambda i: abs(visible[i] - primary[-1].close))
            if atm_i < 2 or atm_i + 2 >= len(visible):
                active = False
                now += timedelta(minutes=5)
                continue
            strikes = visible[atm_i-2:atm_i+3]
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
            if any(not item.ce_candles or not item.pe_candles for item in window):
                active = False
                now += timedelta(minutes=5)
                continue

            evaluations += 1
            atm = window[2]
            atm_assessment = assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
            window_assessment = assess_strike_window_confirmation(primary, window)
            qualifies = (
                atm_assessment.signal == ATMConfirmationSignal.CONFIRM_CE
                and atm_assessment.agreement_score >= ATM_MIN_SCORE
                and window_assessment.signal == StrikeWindowSignal.CONFIRM_CE
                and window_assessment.agreement_score >= WINDOW_MIN_SCORE
            )
            if qualifies:
                qualifying_checkpoints += 1
                if not active:
                    mfe, mae, m5, m10, m15, result, entry = _outcome(day_spot, now)
                    signals.append(Signal(now, round(atm_assessment.agreement_score,1), round(window_assessment.agreement_score,1), round(entry,2), round(mfe,2), round(mae,2), round(m5,2), round(m10,2), round(m15,2), result))
                active = True
            else:
                active = False
            now += timedelta(minutes=5)

    wins = sum(s.target_10_stop_5 == "WIN" for s in signals)
    losses = sum(s.target_10_stop_5 == "LOSS" for s in signals)
    def rate(n):
        return round(100.0*n/len(signals),2) if signals else 0.0
    return {
        "system": SYSTEM,
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "rule_locked_before_validation": True,
        "rule": "CE only; Nandi ATM CONFIRM_CE score >=95 AND Nandi ATM±2 CONFIRM_CE score >=80 at the same completed 5m checkpoint; continuous confirmations collapsed to one signal cluster",
        "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop counted as loss",
        "tested_days": len(tested_days),
        "skipped_days_without_near_expiry": skipped_days,
        "evaluations": evaluations,
        "qualifying_checkpoints": qualifying_checkpoints,
        "unique_signal_clusters": len(signals),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": rate(wins),
        "loss_rate_pct": rate(losses),
        "mfe_5pt_hit_rate_pct": rate(sum(s.mfe_points >= 5 for s in signals)),
        "mfe_10pt_hit_rate_pct": rate(sum(s.mfe_points >= 10 for s in signals)),
        "continuation_5m_pct": rate(sum(s.move_5m > 0 for s in signals)),
        "continuation_10m_pct": rate(sum(s.move_10m > 0 for s in signals)),
        "continuation_15m_pct": rate(sum(s.move_15m > 0 for s in signals)),
        "source": "https://huggingface.co/datasets/thetrademarkk/india-index-options-1m",
        "loaded_expiry_files": loaded,
        "signals": [{**asdict(s), "timestamp": s.timestamp.isoformat()} for s in signals],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2026,7,1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026,7,31))
    p.add_argument("--output", default="nandi_test2_locked_oos.json")
    p.add_argument("--cache", default=".cache/hf-india-options")
    a = p.parse_args()
    payload = run(a.start, a.end, Path(a.cache))
    Path(a.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
