from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from nandi_oi.models import IntradayCandle, OptionStrikeCandles
from nandi_v2.atm_strategy import assess_atm_confirmation
from nandi_v2.engine import decide
from nandi_v2.models import MarketContext, OptionChainSnapshot, OptionLeg, StrikeRow
from nandi_v2.strike_window_strategy import assess_strike_window_confirmation
from shiv_v1.engine import (
    SimilarityStats,
    assess_timeframe,
    build_shiv_decision,
    classify_market_regime,
    combine_timeframes,
    infer_candidate_side,
    next_persistence,
)
from shiv_v2.elite import assess_a_plus_plus_plus_plus
from shiv_v2.replay import WalkForwardResult
from shiv_v2.safety import build_safe_v2_decision
from shiv_v2.strategy import select_option_strike

PUBLIC_SAMPLE_URL = "https://raw.githubusercontent.com/rajmaurya0904/bhav/main/sample_data/nifty_1y_1min.xlsx"
PUBLIC_SAMPLE_PROJECT = "https://github.com/rajmaurya0904/bhav"
CORE_TIMEFRAMES = (1, 3, 5, 15)
PRIMARY_INTERVAL = 5


@dataclass(frozen=True)
class SignalResult:
    timestamp: datetime
    system: str
    direction: str
    setup_quality: float
    mtf_agreement: float
    strike_score: float
    persistence: int
    entry_nifty: float
    mfe_points: float
    mae_points: float
    move_5m: float
    move_10m: float
    move_15m: float
    target_10_stop_5: str


@dataclass
class Tracker:
    prior_side: str = ""
    persistence: int = 0
    first_side: str = ""
    first_strike: float | None = None
    first_seen: datetime | None = None
    first_premium: float | None = None
    last_signal: datetime | None = None


class Report:
    def __init__(self, start: date, end: date) -> None:
        self.start = start
        self.end = end
        self.signals: list[SignalResult] = []
        self.eval_counts = defaultdict(int)
        self.blocker_counts = defaultdict(int)
        self.tested_days: set[date] = set()
        self.missing_window_evals = 0

    @staticmethod
    def _rate(values) -> float:
        values = list(values)
        return round(100.0 * sum(bool(v) for v in values) / len(values), 2) if values else 0.0

    def summary_for(self, system: str) -> dict[str, object]:
        rows = [row for row in self.signals if row.system == system]
        wins = sum(row.target_10_stop_5 == "WIN" for row in rows)
        losses = sum(row.target_10_stop_5 == "LOSS" for row in rows)
        timeouts = sum(row.target_10_stop_5 == "TIMEOUT" for row in rows)
        return {
            "system": system,
            "signals": len(rows),
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
            "evaluations": self.eval_counts[system],
        }

    def payload(self) -> dict[str, object]:
        return {
            "from_date": self.start.isoformat(),
            "to_date": self.end.isoformat(),
            "tested_days": len(self.tested_days),
            "source": PUBLIC_SAMPLE_PROJECT,
            "primary_timeframe_minutes": PRIMARY_INTERVAL,
            "benchmark": "+10 NIFTY points before -5 within 15 minutes; same 1m candle target+stop is a conservative LOSS",
            "methodology": "Candle-by-candle replay using current SHIV V2 / A++++ market-rule functions from the shiv branch. M/W remains excluded through build_safe_v2_decision.",
            "shiv_data_note": "Public source has OHLC/volume/OI but no historical bid/ask. SHIV natively treats spread as unavailable, so no spread is invented.",
            "aplus_data_note": "A++++ requires a verified <=1.5% spread, which the public source does not contain. A++++_PERFECT_SPREAD_UPPER_BOUND sets bid=ask=historical close only to test all remaining current gates. It is intentionally optimistic and is not a full A++++ validation.",
            "missing_complete_atm_plusminus2_evaluations": self.missing_window_evals,
            "systems": {
                "SHIV_V2": self.summary_for("SHIV_V2"),
                "A++++_PERFECT_SPREAD_UPPER_BOUND": self.summary_for("A++++_PERFECT_SPREAD_UPPER_BOUND"),
            },
            "blocker_counts": dict(sorted(self.blocker_counts.items())),
            "signals": [{**asdict(row), "timestamp": row.timestamp.isoformat()} for row in self.signals],
        }


def download_sample(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    req = Request(PUBLIC_SAMPLE_URL, headers={"User-Agent": "Shiv-Public-Backtest/1.0"})
    with urlopen(req, timeout=120) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    if path.stat().st_size < 1_000_000:
        raise RuntimeError("Public sample workbook download was unexpectedly small")
    return path


def load_frames(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    spot_raw = pd.read_excel(path, sheet_name="Spot_1min", engine="openpyxl")
    spot_ts = pd.to_datetime(
        spot_raw["Date"].astype(str).str.slice(0, 10) + " " + spot_raw["Time"].astype(str), errors="coerce"
    )
    spot = pd.DataFrame({
        "timestamp": spot_ts,
        "open": pd.to_numeric(spot_raw["Open"], errors="coerce"),
        "high": pd.to_numeric(spot_raw["High"], errors="coerce"),
        "low": pd.to_numeric(spot_raw["Low"], errors="coerce"),
        "close": pd.to_numeric(spot_raw["Close"], errors="coerce"),
        "volume": pd.to_numeric(spot_raw.get("Volume", 0), errors="coerce").fillna(0),
        "oi": 0.0,
    }).dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    spot["day"] = spot["timestamp"].dt.date

    opt_raw = pd.read_excel(path, sheet_name="ATM_Options_1min", engine="openpyxl")
    opt_ts = pd.to_datetime(opt_raw["Timestamp"], utc=True, errors="coerce").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    opt = pd.DataFrame({
        "timestamp": opt_ts,
        "day": pd.to_datetime(opt_raw["Date"], errors="coerce").dt.date,
        "expiry": pd.to_datetime(opt_raw["Expiry"], errors="coerce").dt.date,
        "strike": pd.to_numeric(opt_raw["Strike"], errors="coerce"),
        "side": opt_raw["Type"].astype(str).str.upper().str.strip(),
        "open": pd.to_numeric(opt_raw["Open"], errors="coerce"),
        "high": pd.to_numeric(opt_raw["High"], errors="coerce"),
        "low": pd.to_numeric(opt_raw["Low"], errors="coerce"),
        "close": pd.to_numeric(opt_raw["Close"], errors="coerce"),
        "volume": pd.to_numeric(opt_raw.get("Volume", 0), errors="coerce").fillna(0),
        "oi": pd.to_numeric(opt_raw.get("OI", 0), errors="coerce").fillna(0),
    }).dropna(subset=["timestamp", "day", "expiry", "strike", "open", "high", "low", "close"])
    opt["strike"] = opt["strike"].astype(int)
    return spot, opt.sort_values(["timestamp", "strike", "side"])


def candle_from(row) -> IntradayCandle:
    return IntradayCandle(
        timestamp=row.timestamp, open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close),
        volume=float(getattr(row, "volume", 0) or 0), open_interest=float(getattr(row, "oi", 0) or 0),
    )


def bucket_start(ts: datetime, minutes: int) -> datetime:
    anchor = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    elapsed = int((ts - anchor).total_seconds() // 60)
    return anchor + timedelta(minutes=(max(0, elapsed) // minutes) * minutes)


def aggregate(rows: list[IntradayCandle], minutes: int, now: datetime) -> tuple[IntradayCandle, ...]:
    buckets: dict[datetime, list[IntradayCandle]] = defaultdict(list)
    for candle in rows:
        if candle.timestamp + timedelta(minutes=1) > now:
            continue
        buckets[bucket_start(candle.timestamp, minutes)].append(candle)
    output = []
    for start in sorted(buckets):
        if start + timedelta(minutes=minutes) > now:
            continue
        items = buckets[start]
        output.append(IntradayCandle(
            timestamp=start,
            open=items[0].open,
            high=max(item.high for item in items),
            low=min(item.low for item in items),
            close=items[-1].close,
            volume=sum(item.volume for item in items),
            open_interest=items[-1].open_interest,
        ))
    return tuple(output)


def rsi(candles: tuple[IntradayCandle, ...], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    changes = [b.close - a.close for a, b in zip(candles[:-1], candles[1:])][-period:]
    gains = sum(max(x, 0.0) for x in changes) / period
    losses = sum(max(-x, 0.0) for x in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def option_series(day_opt: pd.DataFrame, strike: int, side: str, now: datetime) -> list[IntradayCandle]:
    rows = day_opt[(day_opt["strike"] == strike) & (day_opt["side"] == side) & (day_opt["timestamp"] < now)]
    return [candle_from(row) for row in rows.itertuples(index=False)]


def choose_window(day_opt: pd.DataFrame, spot: float, now: datetime) -> tuple[list[int], date] | None:
    visible = day_opt[day_opt["timestamp"] < now]
    strikes = sorted(set(visible[visible["side"] == "CE"]["strike"]) & set(visible[visible["side"] == "PE"]["strike"]))
    if len(strikes) < 5:
        return None
    atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    if atm_i < 2 or atm_i + 2 >= len(strikes):
        return None
    selected = strikes[atm_i - 2:atm_i + 3]
    expiry = visible[visible["strike"] == strikes[atm_i]]["expiry"].iloc[-1]
    return selected, expiry


def build_window(day_opt: pd.DataFrame, strikes: list[int], expiry: date, now: datetime) -> tuple[OptionStrikeCandles, ...]:
    rows = []
    for offset, strike in enumerate(strikes, start=-2):
        ce = aggregate(option_series(day_opt, strike, "CE", now), PRIMARY_INTERVAL, now)
        pe = aggregate(option_series(day_opt, strike, "PE", now), PRIMARY_INTERVAL, now)
        rows.append(OptionStrikeCandles(float(strike), expiry.isoformat(), offset, ce, pe))
    return tuple(rows)


def previous_contract_reference(all_opt: pd.DataFrame, day: date, expiry: date, strike: int, side: str) -> tuple[float, float] | None:
    prior = all_opt[(all_opt["timestamp"] < datetime.combine(day, time.min)) & (all_opt["expiry"] == expiry) & (all_opt["strike"] == strike) & (all_opt["side"] == side)]
    if prior.empty:
        return None
    row = prior.iloc[-1]
    return float(row["close"]), float(row["oi"])


def build_snapshot(all_opt: pd.DataFrame, day_opt: pd.DataFrame, strikes: list[int], expiry: date, spot: float, now: datetime, perfect_spread: bool) -> OptionChainSnapshot:
    rows_out = []
    for strike in strikes:
        legs = {}
        for side in ("CE", "PE"):
            visible = day_opt[(day_opt["strike"] == strike) & (day_opt["side"] == side) & (day_opt["timestamp"] < now)]
            if visible.empty:
                current_close = current_oi = current_volume = 0.0
                prior_close = prior_oi = 0.0
            else:
                current = visible.iloc[-1]
                current_close, current_oi, current_volume = float(current["close"]), float(current["oi"]), float(current["volume"])
                ref = previous_contract_reference(all_opt, day_opt["day"].iloc[0], expiry, strike, side)
                if ref is None:
                    first = visible.iloc[0]
                    prior_close, prior_oi = float(first["close"]), float(first["oi"])
                else:
                    prior_close, prior_oi = ref
            bid = ask = current_close if perfect_spread and current_close > 0 else 0.0
            legs[side] = OptionLeg(
                ltp=current_close,
                change=current_close - prior_close,
                oi=current_oi,
                change_oi=current_oi - prior_oi,
                volume=current_volume,
                iv=0.0,
                bid=bid,
                ask=ask,
            )
        rows_out.append(StrikeRow(float(strike), legs["CE"], legs["PE"]))
    return OptionChainSnapshot(now, expiry.isoformat(), spot, tuple(rows_out), source="BHAV_PUBLIC")


def oi_engine(snapshot: OptionChainSnapshot, primary: tuple[IntradayCandle, ...], now: datetime) -> tuple[str, float]:
    if not primary:
        return "NONE", 0.0
    recent = primary[-8:]
    context = MarketContext(
        observed_at=now,
        previous_spot=primary[-1].close,
        recent_high=max(item.high for item in recent),
        recent_low=min(item.low for item in recent),
        momentum_rsi=rsi(primary),
    )
    raw = decide(snapshot, context, trade_threshold=65.0, prepare_threshold=55.0, minimum_edge=5.0)
    edge = abs(raw.ce_score - raw.pe_score)
    if edge < 5.0:
        return "NONE", max(raw.ce_score, raw.pe_score)
    return ("CE" if raw.ce_score > raw.pe_score else "PE"), max(raw.ce_score, raw.pe_score)


def update_tracker(tracker: Tracker, side: str, strike: float | None, premium: float | None, now: datetime) -> tuple[int, datetime | None, float | None]:
    new_side, count = next_persistence(tracker.prior_side, tracker.persistence, side)
    tracker.prior_side, tracker.persistence = new_side, count
    if side not in {"CE", "PE"} or strike is None or premium is None or premium <= 0:
        tracker.first_side, tracker.first_strike, tracker.first_seen, tracker.first_premium = "", None, None, None
    elif tracker.first_side != side or tracker.first_strike != strike:
        tracker.first_side, tracker.first_strike = side, strike
        tracker.first_seen, tracker.first_premium = now, premium
    return count, tracker.first_seen, tracker.first_premium


def outcome(day_spot: list[IntradayCandle], now: datetime, direction: str) -> tuple[float, float, float, float, float, str, float]:
    past = [c for c in day_spot if c.timestamp + timedelta(minutes=1) <= now]
    future = [c for c in day_spot if now <= c.timestamp < now + timedelta(minutes=15)]
    if not past or not future:
        return 0, 0, 0, 0, 0, "TIMEOUT", past[-1].close if past else 0.0
    entry = past[-1].close
    signed_highs, signed_lows = [], []
    result = "TIMEOUT"
    for c in future:
        if direction == "CE":
            fav, adv = c.high - entry, entry - c.low
        else:
            fav, adv = entry - c.low, c.high - entry
        signed_highs.append(fav); signed_lows.append(adv)
        hit_t, hit_s = fav >= 10, adv >= 5
        if hit_s:
            result = "LOSS"; break
        if hit_t:
            result = "WIN"; break
    def move(minutes: int) -> float:
        later = [c for c in day_spot if c.timestamp + timedelta(minutes=1) <= now + timedelta(minutes=minutes)]
        if not later:
            return 0.0
        delta = later[-1].close - entry
        return delta if direction == "CE" else -delta
    return max(signed_highs or [0.0]), max(signed_lows or [0.0]), move(5), move(10), move(15), result, entry


def dummy_walk_forward() -> WalkForwardResult:
    return WalkForwardResult("UNVALIDATED", 0, tuple(), 0, 0, None, None, None, 0, "Public replay tests live gates only.")


def evaluate_system(
    *,
    system: str,
    perfect_spread: bool,
    all_opt: pd.DataFrame,
    day_opt: pd.DataFrame,
    day_spot: list[IntradayCandle],
    spot_by_tf: dict[int, tuple[IntradayCandle, ...]],
    window: tuple[OptionStrikeCandles, ...],
    strikes: list[int],
    expiry: date,
    now: datetime,
    tracker: Tracker,
    report: Report,
) -> None:
    primary = spot_by_tf[PRIMARY_INTERVAL]
    if len(primary) < 6:
        return
    mtf_rows = tuple(assess_timeframe(tf, spot_by_tf[tf]) for tf in CORE_TIMEFRAMES)
    mtf = combine_timeframes(mtf_rows)
    primary_regime = classify_market_regime(primary)
    atm = next((item for item in window if item.offset == 0), None)
    if atm is None:
        return
    atm_assessment = assess_atm_confirmation(primary, atm.ce_candles, atm.pe_candles)
    strike_assessment = assess_strike_window_confirmation(primary, window)
    spot = primary[-1].close
    snapshot = build_snapshot(all_opt, day_opt, strikes, expiry, spot, now, perfect_spread)
    oi_side, oi_score = oi_engine(snapshot, primary, now)
    candidate_side = infer_candidate_side(mtf, atm_assessment, strike_assessment, oi_side)
    preview = select_option_strike(snapshot, window, candidate_side)
    premium = preview.selected.premium if preview.selected else None
    selected_strike = preview.selected.strike if preview.selected else None
    persistence, first_seen, first_premium = update_tracker(tracker, candidate_side, selected_strike, premium, now)
    atm_candles = atm.ce_candles if candidate_side == "CE" else atm.pe_candles if candidate_side == "PE" else tuple()
    atm_spread = 0.0 if perfect_spread and candidate_side in {"CE", "PE"} else None
    base = build_shiv_decision(
        interval_minutes=PRIMARY_INTERVAL,
        primary_regime=primary_regime,
        mtf=mtf,
        atm=atm_assessment,
        strike=strike_assessment,
        oi_side=oi_side,
        oi_score=oi_score,
        candidate_side=candidate_side,
        persistence_count=persistence,
        option_spread_pct=atm_spread,
        option_strike=atm.strike,
        option_candles=atm_candles,
        similarity=SimilarityStats(),
    )
    decision = build_safe_v2_decision(
        base=base,
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
    actionable = decision.actionable
    if system.startswith("A++++"):
        elite = assess_a_plus_plus_plus_plus(decision, SimilarityStats(), dummy_walk_forward())
        actionable = elite.live_candidate
        for blocker in elite.blockers:
            if not blocker.startswith(("Needs at least", "Comparable observed", "Comparable setups", "95% Wilson", "Walk-forward")):
                report.blocker_counts[f"A++++::{blocker}"] += 1
    else:
        for blocker in decision.blockers:
            report.blocker_counts[f"SHIV::{blocker}"] += 1
    if not actionable or decision.entry_plan.status != "ENTRY READY" or decision.side not in {"CE", "PE"}:
        return
    if tracker.last_signal and now - tracker.last_signal < timedelta(minutes=5):
        return
    tracker.last_signal = now
    mfe, mae, m5, m10, m15, result, entry = outcome(day_spot, now, decision.side)
    selected = decision.strike_selection.selected
    report.signals.append(SignalResult(
        now, system, decision.side, round(decision.setup_quality, 1), round(decision.base.mtf_agreement, 1),
        round(selected.score if selected else 0.0, 1), decision.base.persistence_count, round(entry, 2),
        round(mfe, 2), round(mae, 2), round(m5, 2), round(m10, 2), round(m15, 2), result,
    ))


def run(start: date, end: date, workbook: Path) -> Report:
    spot_df, opt_df = load_frames(download_sample(workbook))
    report = Report(start, end)
    days = sorted(day for day in set(spot_df["day"]) if start <= day <= end)
    for day in days:
        day_spot_df = spot_df[spot_df["day"] == day]
        day_opt = opt_df[opt_df["day"] == day]
        if day_spot_df.empty or day_opt.empty:
            continue
        day_spot = [candle_from(row) for row in day_spot_df.itertuples(index=False)]
        report.tested_days.add(day)
        shiv_tracker, a4_tracker = Tracker(), Tracker()
        start_ts = datetime.combine(day, time(9, 20))
        end_ts = datetime.combine(day, time(15, 25))
        now = start_ts
        while now <= end_ts:
            completed_1m = tuple(c for c in day_spot if c.timestamp + timedelta(minutes=1) <= now)
            if not completed_1m:
                now += timedelta(minutes=5); continue
            spot_by_tf = {1: completed_1m}
            for tf in (3, 5, 15):
                spot_by_tf[tf] = aggregate(day_spot, tf, now)
            spot_now = completed_1m[-1].close
            chosen = choose_window(day_opt, spot_now, now)
            if chosen is None:
                report.missing_window_evals += 1
                now += timedelta(minutes=5); continue
            strikes, expiry = chosen
            window = build_window(day_opt, strikes, expiry, now)
            if any(not item.ce_candles or not item.pe_candles for item in window):
                report.missing_window_evals += 1
                now += timedelta(minutes=5); continue
            evaluate_system(system="SHIV_V2", perfect_spread=False, all_opt=opt_df, day_opt=day_opt, day_spot=day_spot, spot_by_tf=spot_by_tf, window=window, strikes=strikes, expiry=expiry, now=now, tracker=shiv_tracker, report=report)
            evaluate_system(system="A++++_PERFECT_SPREAD_UPPER_BOUND", perfect_spread=True, all_opt=opt_df, day_opt=day_opt, day_spot=day_spot, spot_by_tf=spot_by_tf, window=window, strikes=strikes, expiry=expiry, now=now, tracker=a4_tracker, report=report)
            now += timedelta(minutes=5)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--output", default="shiv_aplus_public_backtest_results.json")
    parser.add_argument("--workbook", default=".cache/bhav/nifty_1y_1min.xlsx")
    args = parser.parse_args()
    report = run(args.start, args.end, Path(args.workbook))
    payload = report.payload()
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
