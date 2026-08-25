from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

import pandas as pd

from nandi_oi.models import IntradayCandle
from .continuation import Test1Signal, assess_test1_continuation

PUBLIC_SAMPLE_URL = (
    "https://raw.githubusercontent.com/rajmaurya0904/bhav/main/"
    "sample_data/nifty_1y_1min.xlsx"
)
PUBLIC_SAMPLE_PROJECT = "https://github.com/rajmaurya0904/bhav"
PUBLIC_SAMPLE_LICENSE = "MIT"


@dataclass(frozen=True)
class PublicTradeResult:
    timestamp: datetime
    direction: str
    score: float
    strike: int
    entry: float
    mfe_points: float
    mae_points: float
    move_5m: float
    move_10m: float
    move_15m: float
    target_5_stop_5: str
    target_10_stop_5: str
    target_15_stop_7: str
    target_20_stop_10: str


@dataclass(frozen=True)
class PublicBacktestReport:
    from_date: date
    to_date: date
    source: str
    dataset_note: str
    trades: tuple[PublicTradeResult, ...]
    prepare_signals: int
    late_skip_clusters: int
    late_skip_avoided_10_5: int
    late_skip_missed_10_5: int
    late_skip_neutral_10_5: int
    unavailable_minutes: int
    tested_days: int
    skipped_days: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.trades)

    @staticmethod
    def _rate(values: Iterable[bool]) -> float:
        values = tuple(values)
        return round(100.0 * sum(values) / len(values), 2) if values else 0.0

    def hit_rate(self, points: float) -> float:
        return self._rate(t.mfe_points >= points for t in self.trades)

    def continuation_rate(self, minutes: int) -> float:
        attr = {5: "move_5m", 10: "move_10m", 15: "move_15m"}[minutes]
        return self._rate(getattr(t, attr) > 0 for t in self.trades)

    def outcome_rate(self, field: str, outcome: str = "WIN") -> float:
        return self._rate(getattr(t, field) == outcome for t in self.trades)

    def direction_rate(self, direction: str, field: str = "target_10_stop_5") -> float:
        sample = [t for t in self.trades if t.direction == direction]
        return self._rate(getattr(t, field) == "WIN" for t in sample)

    def as_summary(self) -> dict[str, object]:
        primary_wins = sum(t.target_10_stop_5 == "WIN" for t in self.trades)
        primary_losses = sum(t.target_10_stop_5 == "LOSS" for t in self.trades)
        primary_timeouts = sum(t.target_10_stop_5 == "TIMEOUT" for t in self.trades)
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "source": self.source,
            "dataset_note": self.dataset_note,
            "tested_days": self.tested_days,
            "total_confirmed_signals": self.total,
            "primary_benchmark": "10 NIFTY-point target before 5-point stop within 15 minutes; if both touch in one 1m candle, counted as loss (conservative)",
            "primary_wins": primary_wins,
            "primary_losses": primary_losses,
            "primary_timeouts": primary_timeouts,
            "primary_win_rate_pct": self.outcome_rate("target_10_stop_5", "WIN"),
            "primary_loss_rate_pct": self.outcome_rate("target_10_stop_5", "LOSS"),
            "ce_primary_win_rate_pct": self.direction_rate("CE"),
            "pe_primary_win_rate_pct": self.direction_rate("PE"),
            "mfe_hit_rate_5pt_pct": self.hit_rate(5),
            "mfe_hit_rate_10pt_pct": self.hit_rate(10),
            "mfe_hit_rate_15pt_pct": self.hit_rate(15),
            "mfe_hit_rate_20pt_pct": self.hit_rate(20),
            "continuation_5m_pct": self.continuation_rate(5),
            "continuation_10m_pct": self.continuation_rate(10),
            "continuation_15m_pct": self.continuation_rate(15),
            "benchmark_5_target_5_stop_win_pct": self.outcome_rate("target_5_stop_5", "WIN"),
            "benchmark_15_target_7_stop_win_pct": self.outcome_rate("target_15_stop_7", "WIN"),
            "benchmark_20_target_10_stop_win_pct": self.outcome_rate("target_20_stop_10", "WIN"),
            "prepare_signals": self.prepare_signals,
            "late_skip_clusters": self.late_skip_clusters,
            "late_skip_avoided_10_5": self.late_skip_avoided_10_5,
            "late_skip_missed_10_5": self.late_skip_missed_10_5,
            "late_skip_neutral_10_5": self.late_skip_neutral_10_5,
            "unavailable_minutes": self.unavailable_minutes,
            "skipped_days": list(self.skipped_days),
        }

    def to_json(self) -> str:
        payload = self.as_summary()
        payload["trades"] = [
            {
                **asdict(t),
                "timestamp": t.timestamp.isoformat(),
            }
            for t in self.trades
        ]
        return json.dumps(payload, indent=2)


def _download_public_sample(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    req = Request(PUBLIC_SAMPLE_URL, headers={"User-Agent": "Shiv-TEST1-Research/1.0"})
    with urlopen(req, timeout=120) as response, path.open("wb") as target:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
    if path.stat().st_size < 1_000_000:
        raise RuntimeError("Public sample workbook download was unexpectedly small")
    return path


def _parse_spot_frame(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Spot_1min", engine="openpyxl")
    ts = pd.to_datetime(
        raw["Date"].astype(str).str.slice(0, 10) + " " + raw["Time"].astype(str),
        errors="coerce",
    )
    frame = pd.DataFrame(
        {
            "timestamp": ts,
            "open": pd.to_numeric(raw["Open"], errors="coerce"),
            "high": pd.to_numeric(raw["High"], errors="coerce"),
            "low": pd.to_numeric(raw["Low"], errors="coerce"),
            "close": pd.to_numeric(raw["Close"], errors="coerce"),
            "volume": pd.to_numeric(raw.get("Volume", 0), errors="coerce").fillna(0),
            "open_interest": 0.0,
        }
    )
    return frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")


def _parse_option_frame(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="ATM_Options_1min", engine="openpyxl")
    ts = pd.to_datetime(raw["Timestamp"], utc=True, errors="coerce")
    ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    frame = pd.DataFrame(
        {
            "timestamp": ts,
            "day": pd.to_datetime(raw["Date"], errors="coerce").dt.date,
            "expiry": pd.to_datetime(raw["Expiry"], errors="coerce").dt.date,
            "strike": pd.to_numeric(raw["Strike"], errors="coerce"),
            "option_type": raw["Type"].astype(str).str.upper().str.strip(),
            "open": pd.to_numeric(raw["Open"], errors="coerce"),
            "high": pd.to_numeric(raw["High"], errors="coerce"),
            "low": pd.to_numeric(raw["Low"], errors="coerce"),
            "close": pd.to_numeric(raw["Close"], errors="coerce"),
            "volume": pd.to_numeric(raw.get("Volume", 0), errors="coerce").fillna(0),
            "open_interest": pd.to_numeric(raw.get("OI", 0), errors="coerce").fillna(0),
        }
    )
    frame = frame.dropna(subset=["timestamp", "day", "strike", "open", "high", "low", "close"])
    frame["strike"] = frame["strike"].astype(int)
    return frame.sort_values(["timestamp", "option_type", "strike"])


def _row_to_candle(row: object) -> IntradayCandle:
    return IntradayCandle(
        timestamp=row.timestamp,
        open=float(row.open),
        high=float(row.high),
        low=float(row.low),
        close=float(row.close),
        volume=float(getattr(row, "volume", 0.0) or 0.0),
        open_interest=float(getattr(row, "open_interest", 0.0) or 0.0),
    )


def _bucket_start(ts: datetime, minutes: int) -> datetime:
    session = ts.replace(hour=9, minute=15, second=0, microsecond=0)
    elapsed = max(0, int((ts - session).total_seconds() // 60))
    return session + timedelta(minutes=(elapsed // minutes) * minutes)


def _update_aggregate(
    series: list[IntradayCandle], candle: IntradayCandle, minutes: int
) -> None:
    bucket = _bucket_start(candle.timestamp, minutes)
    if not series or series[-1].timestamp != bucket:
        series.append(
            IntradayCandle(
                timestamp=bucket,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                open_interest=candle.open_interest,
            )
        )
        return
    prev = series[-1]
    series[-1] = IntradayCandle(
        timestamp=bucket,
        open=prev.open,
        high=max(prev.high, candle.high),
        low=min(prev.low, candle.low),
        close=candle.close,
        volume=prev.volume + candle.volume,
        open_interest=candle.open_interest,
    )


def _window_outcome(
    future: tuple[IntradayCandle, ...],
    entry: float,
    direction: str,
    target_points: float,
    stop_points: float,
) -> str:
    for candle in future:
        if direction == "CE":
            target_hit = candle.high >= entry + target_points
            stop_hit = candle.low <= entry - stop_points
        else:
            target_hit = candle.low <= entry - target_points
            stop_hit = candle.high >= entry + stop_points
        if stop_hit:
            return "LOSS"
        if target_hit:
            return "WIN"
    return "TIMEOUT"


def _future_metrics(
    day_spot: tuple[IntradayCandle, ...], idx: int, direction: str
) -> tuple[float, float, float, float, float, tuple[IntradayCandle, ...]]:
    entry = day_spot[idx].close
    future = day_spot[idx + 1 : min(len(day_spot), idx + 16)]
    if not future:
        return 0.0, 0.0, 0.0, 0.0, 0.0, ()
    if direction == "CE":
        mfe = max(c.high - entry for c in future)
        mae = max(entry - c.low for c in future)
    else:
        mfe = max(entry - c.low for c in future)
        mae = max(c.high - entry for c in future)

    def move(minutes: int) -> float:
        target_idx = idx + minutes
        if target_idx >= len(day_spot):
            return 0.0
        end = day_spot[target_idx].close
        return end - entry if direction == "CE" else entry - end

    return mfe, mae, move(5), move(10), move(15), future


def _nearest_common_strike(
    strikes_by_side: dict[str, set[int]], spot: float
) -> int | None:
    common = strikes_by_side.get("CE", set()) & strikes_by_side.get("PE", set())
    if not common:
        return None
    return min(common, key=lambda strike: abs(strike - spot))


def run_public_test1_backtest(
    from_date: date,
    to_date: date,
    *,
    cache_path: str | Path = "/tmp/shiv_test1_public/nifty_1y_1min.xlsx",
    cluster_minutes: int = 5,
) -> PublicBacktestReport:
    if from_date > to_date:
        raise ValueError("from_date must be on or before to_date")

    path = _download_public_sample(Path(cache_path))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)

    spot_df = spot_df[
        (spot_df["timestamp"].dt.date >= from_date)
        & (spot_df["timestamp"].dt.date <= to_date)
    ]
    opt_df = opt_df[(opt_df["day"] >= from_date) & (opt_df["day"] <= to_date)]
    if spot_df.empty or opt_df.empty:
        raise RuntimeError("Public workbook has no overlapping NIFTY and option data for the requested window")

    option_rows_by_day: dict[date, list[object]] = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows_by_day[row.day].append(row)

    spot_rows_by_day: dict[date, tuple[IntradayCandle, ...]] = {}
    for day_value, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True):
        spot_rows_by_day[day_value] = tuple(_row_to_candle(row) for row in group.itertuples(index=False))

    trades: list[PublicTradeResult] = []
    prepare_signals = 0
    late_skip_clusters = 0
    late_avoided = 0
    late_missed = 0
    late_neutral = 0
    unavailable = 0
    tested_days = 0
    skipped_days: list[str] = []

    for day_value in sorted(spot_rows_by_day):
        day_spot = spot_rows_by_day[day_value]
        option_rows = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 60 or not option_rows:
            skipped_days.append(day_value.isoformat())
            continue

        options_at_time: dict[datetime, list[object]] = defaultdict(list)
        strikes_by_side: dict[str, set[int]] = {"CE": set(), "PE": set()}
        for row in option_rows:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            options_at_time[row.timestamp].append((side, row))
            strikes_by_side[side].add(int(row.strike))

        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            skipped_days.append(day_value.isoformat())
            continue

        tested_days += 1
        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        n15: list[IntradayCandle] = []
        option_history: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        last_confirmed_ts: datetime | None = None
        last_late_ts: datetime | None = None

        for idx, candle in enumerate(day_spot):
            n1.append(candle)
            _update_aggregate(n5, candle, 5)
            _update_aggregate(n15, candle, 15)

            for side, row in options_at_time.get(candle.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))

            strike = _nearest_common_strike(strikes_by_side, candle.close)
            if strike is None:
                unavailable += 1
                continue
            ce = option_history.get(("CE", strike), [])
            pe = option_history.get(("PE", strike), [])
            if min(len(n1), len(n5), len(n15), len(ce), len(pe)) < 4:
                unavailable += 1
                continue

            assessment = assess_test1_continuation(
                n1[-20:], n5[-8:], n15[-6:], ce[-20:], pe[-20:]
            )

            if assessment.signal in (Test1Signal.PREPARE_CE, Test1Signal.PREPARE_PE):
                prepare_signals += 1

            if assessment.signal in (Test1Signal.LATE_SKIP_CE, Test1Signal.LATE_SKIP_PE):
                if last_late_ts is None or (candle.timestamp - last_late_ts) >= timedelta(minutes=cluster_minutes):
                    last_late_ts = candle.timestamp
                    late_skip_clusters += 1
                    _, _, _, _, _, future = _future_metrics(day_spot, idx, assessment.direction)
                    outcome = _window_outcome(future, candle.close, assessment.direction, 10, 5)
                    if outcome == "LOSS":
                        late_avoided += 1
                    elif outcome == "WIN":
                        late_missed += 1
                    else:
                        late_neutral += 1
                continue

            if assessment.signal not in (Test1Signal.CONFIRMED_CE, Test1Signal.CONFIRMED_PE):
                continue
            if last_confirmed_ts and (candle.timestamp - last_confirmed_ts) < timedelta(minutes=cluster_minutes):
                continue
            if idx + 15 >= len(day_spot):
                continue
            last_confirmed_ts = candle.timestamp

            mfe, mae, move_5m, move_10m, move_15m, future = _future_metrics(
                day_spot, idx, assessment.direction
            )
            trades.append(
                PublicTradeResult(
                    timestamp=candle.timestamp,
                    direction=assessment.direction,
                    score=assessment.score,
                    strike=strike,
                    entry=round(candle.close, 2),
                    mfe_points=round(mfe, 2),
                    mae_points=round(mae, 2),
                    move_5m=round(move_5m, 2),
                    move_10m=round(move_10m, 2),
                    move_15m=round(move_15m, 2),
                    target_5_stop_5=_window_outcome(future, candle.close, assessment.direction, 5, 5),
                    target_10_stop_5=_window_outcome(future, candle.close, assessment.direction, 10, 5),
                    target_15_stop_7=_window_outcome(future, candle.close, assessment.direction, 15, 7),
                    target_20_stop_10=_window_outcome(future, candle.close, assessment.direction, 20, 10),
                )
            )

    return PublicBacktestReport(
        from_date=from_date,
        to_date=to_date,
        source=PUBLIC_SAMPLE_PROJECT,
        dataset_note=(
            "Bhav public offline sample: NIFTY 50 1-minute spot plus nearest-weekly option candles. "
            "The project documents Jul-2025 through Jun-2026 coverage; June 2026 includes ATM ±2 strikes."
        ),
        trades=tuple(trades),
        prepare_signals=prepare_signals,
        late_skip_clusters=late_skip_clusters,
        late_skip_avoided_10_5=late_avoided,
        late_skip_missed_10_5=late_missed,
        late_skip_neutral_10_5=late_neutral,
        unavailable_minutes=unavailable,
        tested_days=tested_days,
        skipped_days=tuple(skipped_days),
    )
