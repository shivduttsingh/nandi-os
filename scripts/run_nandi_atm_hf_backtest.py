from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from nandi_v2.atm_strategy import ATMConfirmationSignal, assess_atm_confirmation
from nandi_v2.strike_window_strategy import StrikeWindowSignal, assess_strike_window_confirmation
from scripts import run_shiv_aplus_public_backtest as base
from scripts.run_shiv_aplus_hf_backtest import HF_REPO, load_hf_frames

PRIMARY_INTERVAL = 5
SYSTEM_ATM = "NANDI_ATM"
SYSTEM_WINDOW = "NANDI_ATM_PLUS_MINUS_2"


@dataclass(frozen=True)
class SignalResult:
    timestamp: datetime
    system: str
    direction: str
    score: float
    entry_nifty: float
    mfe_points: float
    mae_points: float
    move_5m: float
    move_10m: float
    move_15m: float
    target_10_stop_5: str


@dataclass
class ClusterTracker:
    active_side: str = ""


class Report:
    def __init__(self, start: date, end: date) -> None:
        self.start = start
        self.end = end
        self.signals: list[SignalResult] = []
        self.evaluations = {SYSTEM_ATM: 0, SYSTEM_WINDOW: 0}
        self.confirmed_checkpoints = {SYSTEM_ATM: 0, SYSTEM_WINDOW: 0}
        self.tested_days: set[date] = set()
        self.missing_window_evaluations = 0

    @staticmethod
    def _rate(values) -> float:
        values = list(values)
        return round(100.0 * sum(bool(v) for v in values) / len(values), 2) if values else 0.0

    def summary(self, system: str) -> dict[str, object]:
        rows = [row for row in self.signals if row.system == system]
        wins = sum(row.target_10_stop_5 == "WIN" for row in rows)
        losses = sum(row.target_10_stop_5 == "LOSS" for row in rows)
        timeouts = sum(row.target_10_stop_5 == "TIMEOUT" for row in rows)
        return {
            "system": system,
            "evaluations": self.evaluations[system],
            "confirmed_checkpoints": self.confirmed_checkpoints[system],
            "unique_signal_clusters": len(rows),
            "wins": wins,
            "losses": losses,
            "timeouts": timeouts,
            "win_rate_pct": self._rate(row.target_10_stop_5 == "WIN" for row in rows),
            "loss_rate_pct": self._rate(row.target_10_stop_5 == "LOSS" for row in rows),
            "ce_win_rate_pct": self._rate(row.target_10_stop_5 == "WIN" for row in rows if row.direction == "CE"),
            "pe_win_rate_pct": self._rate(row.target_10_stop_5 == "WIN" for row in rows if row.direction == "PE"),
            "mfe_5pt_hit_rate_pct": self._rate(row.mfe_points >= 5 for row in rows),
            "mfe_10pt_hit_rate_pct": self._rate(row.mfe_points >= 10 for row in rows),
            "mfe_15pt_hit_rate_pct": self._rate(row.mfe_points >= 15 for row in rows),
            "mfe_20pt_hit_rate_pct": self._rate(row.mfe_points >= 20 for row in rows),
            "continuation_5m_pct": self._rate(row.move_5m > 0 for row in rows),
            "continuation_10m_pct": self._rate(row.move_10m > 0 for row in rows),
            "continuation_15m_pct": self._rate(row.move_15m > 0 for row in rows),
        }

    def payload(self, loaded_expiries: list[str]) -> dict[str, object]:
        return {
            "from_date": self.start.isoformat(),
            "to_date": self.end.isoformat(),
            "tested_days": len(self.tested_days),
            "source": HF_REPO,
            "source_license": "CC-BY-NC-4.0",
            "loaded_expiry_files": loaded_expiries,
            "primary_timeframe_minutes": PRIMARY_INTERVAL,
            "benchmark": "+10 NIFTY points before -5 within 15 minutes; target+stop in the same 1m candle is counted as LOSS",
            "methodology": (
                "Candle-by-candle replay using the current nandi_v2 ATM and ATM ±2 strategy functions. "
                "The ATM strike/window is selected only from contracts visible at each historical checkpoint. "
                "Continuous same-side confirmations are collapsed into one signal cluster; a new cluster requires the signal to disappear or flip first. "
                "No thresholds are optimized from outcomes."
            ),
            "missing_complete_atm_plusminus2_evaluations": self.missing_window_evaluations,
            "systems": {
                SYSTEM_ATM: self.summary(SYSTEM_ATM),
                SYSTEM_WINDOW: self.summary(SYSTEM_WINDOW),
            },
            "signals": [{**asdict(row), "timestamp": row.timestamp.isoformat()} for row in self.signals],
        }


def _record_if_new(
    *,
    system: str,
    side: str,
    score: float,
    tracker: ClusterTracker,
    report: Report,
    day_spot,
    now: datetime,
) -> None:
    if side not in {"CE", "PE"}:
        tracker.active_side = ""
        return
    report.confirmed_checkpoints[system] += 1
    if tracker.active_side == side:
        return
    tracker.active_side = side
    mfe, mae, m5, m10, m15, result, entry = base.outcome(day_spot, now, side)
    report.signals.append(SignalResult(
        timestamp=now,
        system=system,
        direction=side,
        score=round(score, 1),
        entry_nifty=round(entry, 2),
        mfe_points=round(mfe, 2),
        mae_points=round(mae, 2),
        move_5m=round(m5, 2),
        move_10m=round(m10, 2),
        move_15m=round(m15, 2),
        target_10_stop_5=result,
    ))


def run(start: date, end: date, cache: Path) -> tuple[Report, list[str]]:
    spot_df, opt_df, loaded = load_hf_frames(start, end, cache)
    report = Report(start, end)
    days = sorted(day for day in set(spot_df["day"]) if start <= day <= end)

    for day in days:
        day_spot_df = spot_df[spot_df["day"] == day]
        future_expiries = sorted(e for e in set(opt_df["expiry"]) if e >= day)
        if not future_expiries:
            continue
        expiry = future_expiries[0]
        day_opt = opt_df[(opt_df["day"] == day) & (opt_df["expiry"] == expiry)]
        if day_spot_df.empty or day_opt.empty:
            continue
        day_spot = [base.candle_from(row) for row in day_spot_df.itertuples(index=False)]
        report.tested_days.add(day)
        atm_tracker = ClusterTracker()
        window_tracker = ClusterTracker()

        now = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))
        while now <= end_ts:
            primary = base.aggregate(day_spot, PRIMARY_INTERVAL, now)
            if len(primary) < 4:
                now += timedelta(minutes=5)
                continue
            chosen = base.choose_window(day_opt, primary[-1].close, now)
            if chosen is None:
                report.missing_window_evaluations += 1
                atm_tracker.active_side = ""
                window_tracker.active_side = ""
                now += timedelta(minutes=5)
                continue
            strikes, chosen_expiry = chosen
            window = base.build_window(day_opt, strikes, chosen_expiry, now)
            atm = next((item for item in window if item.offset == 0), None)
            if atm is None or not atm.ce_candles or not atm.pe_candles:
                report.missing_window_evaluations += 1
                atm_tracker.active_side = ""
                window_tracker.active_side = ""
                now += timedelta(minutes=5)
                continue

            report.evaluations[SYSTEM_ATM] += 1
            atm_assessment = assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
            if atm_assessment.signal == ATMConfirmationSignal.CONFIRM_CE:
                atm_side = "CE"
            elif atm_assessment.signal == ATMConfirmationSignal.CONFIRM_PE:
                atm_side = "PE"
            else:
                atm_side = ""
            _record_if_new(
                system=SYSTEM_ATM,
                side=atm_side,
                score=atm_assessment.agreement_score,
                tracker=atm_tracker,
                report=report,
                day_spot=day_spot,
                now=now,
            )

            complete_window = all(item.ce_candles and item.pe_candles for item in window)
            if complete_window:
                report.evaluations[SYSTEM_WINDOW] += 1
                window_assessment = assess_strike_window_confirmation(primary, window)
                if window_assessment.signal == StrikeWindowSignal.CONFIRM_CE:
                    window_side = "CE"
                elif window_assessment.signal == StrikeWindowSignal.CONFIRM_PE:
                    window_side = "PE"
                else:
                    window_side = ""
                _record_if_new(
                    system=SYSTEM_WINDOW,
                    side=window_side,
                    score=window_assessment.agreement_score,
                    tracker=window_tracker,
                    report=report,
                    day_spot=day_spot,
                    now=now,
                )
            else:
                report.missing_window_evaluations += 1
                window_tracker.active_side = ""

            now += timedelta(minutes=5)
    return report, loaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 5, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output", default="nandi_atm_hf_backtest_results.json")
    parser.add_argument("--cache", default=".cache/hf-india-options")
    args = parser.parse_args()
    report, loaded = run(args.start, args.end, Path(args.cache))
    payload = report.payload(loaded)
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
