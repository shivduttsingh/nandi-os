from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from nandi_oi.models import IntradayCandle
from test1.public_backtest import (
    PUBLIC_SAMPLE_PROJECT,
    _download_public_sample,
    _nearest_common_strike,
    _parse_option_frame,
    _parse_spot_frame,
    _row_to_candle,
    _update_aggregate,
)

from .engine import StrategyBSignal, assess_strategy_b


@dataclass(frozen=True)
class StrategyBTrade:
    signal_time: datetime
    entry_time: datetime
    direction: str
    strike: int
    score: float
    opposite_score: float
    entry_premium: float
    exit_premium: float
    outcome: str
    gross_points: float
    net_points: float
    hold_minutes: float


@dataclass(frozen=True)
class StrategyBBacktestReport:
    from_date: date
    to_date: date
    threshold: float
    target_points: float
    stop_points: float
    max_hold_minutes: int
    entry_slippage_points: float
    friction_points: float
    tested_days: int
    trades: tuple[StrategyBTrade, ...]
    watch_signals: int
    blocked_signals: int
    unavailable_minutes: int
    skipped_days: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.trades)

    @staticmethod
    def _rate(values: Iterable[bool]) -> float:
        values = tuple(values)
        return round(100.0 * sum(values) / len(values), 2) if values else 0.0

    @property
    def target_wins(self) -> int:
        return sum(t.outcome == "WIN" for t in self.trades)

    @property
    def stop_losses(self) -> int:
        return sum(t.outcome == "LOSS" for t in self.trades)

    @property
    def timeouts(self) -> int:
        return sum(t.outcome == "TIMEOUT" for t in self.trades)

    @property
    def net_points(self) -> float:
        return round(sum(t.net_points for t in self.trades), 2)

    @property
    def expectancy(self) -> float:
        return round(self.net_points / self.total, 2) if self.total else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(max(0.0, t.net_points) for t in self.trades)
        losses = abs(sum(min(0.0, t.net_points) for t in self.trades))
        if losses == 0:
            return round(gains, 2) if gains > 0 else 0.0
        return round(gains / losses, 2)

    @property
    def max_drawdown_points(self) -> float:
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in self.trades:
            equity += trade.net_points
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return round(max_dd, 2)

    def direction_win_rate(self, direction: str) -> float:
        sample = [t for t in self.trades if t.direction == direction]
        return self._rate(t.outcome == "WIN" for t in sample)

    def as_summary(self) -> dict[str, object]:
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "source": PUBLIC_SAMPLE_PROJECT,
            "threshold": self.threshold,
            "benchmark": f"Buy ATM option on next 1m candle open; +{self.target_points:g} premium-point target / -{self.stop_points:g} premium-point stop; max {self.max_hold_minutes} minutes",
            "execution": f"Entry slippage {self.entry_slippage_points:.2f} premium points; additional round-trip friction {self.friction_points:.2f} premium points; if stop and target touch in the same 1m candle, stop wins",
            "tested_days": self.tested_days,
            "trades": self.total,
            "target_wins": self.target_wins,
            "stop_losses": self.stop_losses,
            "timeouts": self.timeouts,
            "target_win_rate_pct": self._rate(t.outcome == "WIN" for t in self.trades),
            "profitable_trade_rate_pct": self._rate(t.net_points > 0 for t in self.trades),
            "ce_target_win_rate_pct": self.direction_win_rate("CE"),
            "pe_target_win_rate_pct": self.direction_win_rate("PE"),
            "net_points_after_friction": self.net_points,
            "expectancy_points_per_trade": self.expectancy,
            "profit_factor": self.profit_factor,
            "max_drawdown_points": self.max_drawdown_points,
            "watch_signals": self.watch_signals,
            "blocked_signals": self.blocked_signals,
            "unavailable_minutes": self.unavailable_minutes,
            "skipped_days": list(self.skipped_days),
        }

    def to_json(self) -> str:
        payload = self.as_summary()
        payload["trade_log"] = [
            {
                **asdict(trade),
                "signal_time": trade.signal_time.isoformat(),
                "entry_time": trade.entry_time.isoformat(),
            }
            for trade in self.trades
        ]
        return json.dumps(payload, indent=2)


def _trade_outcome(
    candles: tuple[IntradayCandle, ...],
    entry_index: int,
    *,
    target_points: float,
    stop_points: float,
    max_hold_minutes: int,
    entry_slippage_points: float,
    friction_points: float,
) -> tuple[str, float, float, float, float]:
    entry_candle = candles[entry_index]
    entry = entry_candle.open + entry_slippage_points
    target = entry + target_points
    stop = max(0.05, entry - stop_points)
    cutoff = entry_candle.timestamp + timedelta(minutes=max_hold_minutes)
    future = [c for c in candles[entry_index:] if c.timestamp <= cutoff]
    if not future:
        return "TIMEOUT", entry, entry, -friction_points, 0.0

    for candle in future:
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
        # With 1-minute OHLC there is no intrabar ordering. Count an ambiguous candle as a loss.
        if stop_hit:
            net = -stop_points - friction_points
            held = max(0.0, (candle.timestamp - entry_candle.timestamp).total_seconds() / 60.0)
            return "LOSS", entry, stop, net, held
        if target_hit:
            net = target_points - friction_points
            held = max(0.0, (candle.timestamp - entry_candle.timestamp).total_seconds() / 60.0)
            return "WIN", entry, target, net, held

    exit_candle = future[-1]
    gross = exit_candle.close - entry
    net = gross - friction_points
    held = max(0.0, (exit_candle.timestamp - entry_candle.timestamp).total_seconds() / 60.0)
    return "TIMEOUT", entry, exit_candle.close, net, held


def run_public_strategy_b_backtest(
    from_date: date,
    to_date: date,
    *,
    threshold: float = 88.0,
    target_points: float = 10.0,
    stop_points: float = 5.0,
    max_hold_minutes: int = 15,
    cooldown_minutes: int = 15,
    entry_slippage_points: float = 0.25,
    friction_points: float = 0.50,
    cache_path: str | Path = "/tmp/shiv_strategy_b_public/nifty_1y_1min.xlsx",
) -> StrategyBBacktestReport:
    if from_date > to_date:
        raise ValueError("from_date must be on or before to_date")
    if target_points <= 0 or stop_points <= 0 or max_hold_minutes <= 0:
        raise ValueError("target, stop and max_hold_minutes must be positive")

    path = _download_public_sample(Path(cache_path))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    spot_df = spot_df[
        (spot_df["timestamp"].dt.date >= from_date)
        & (spot_df["timestamp"].dt.date <= to_date)
    ]
    opt_df = opt_df[(opt_df["day"] >= from_date) & (opt_df["day"] <= to_date)]
    if spot_df.empty or opt_df.empty:
        raise RuntimeError("Public workbook has no overlapping spot and option data for the requested period")

    option_rows_by_day: dict[date, list[object]] = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows_by_day[row.day].append(row)

    spot_rows_by_day: dict[date, tuple[IntradayCandle, ...]] = {}
    for day_value, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True):
        spot_rows_by_day[day_value] = tuple(_row_to_candle(row) for row in group.itertuples(index=False))

    trades: list[StrategyBTrade] = []
    watch_signals = 0
    blocked_signals = 0
    unavailable = 0
    tested_days = 0
    skipped_days: list[str] = []

    for day_value in sorted(spot_rows_by_day):
        day_spot = spot_rows_by_day[day_value]
        raw_options = option_rows_by_day.get(day_value, [])
        if len(day_spot) < 60 or not raw_options:
            skipped_days.append(day_value.isoformat())
            continue

        options_at_time: dict[datetime, list[tuple[str, object]]] = defaultdict(list)
        option_series_lists: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        strikes_by_side: dict[str, set[int]] = {"CE": set(), "PE": set()}
        for row in raw_options:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side, row))
            option_series_lists[(side, strike)].append(candle)
            strikes_by_side[side].add(strike)

        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            skipped_days.append(day_value.isoformat())
            continue

        option_series = {
            key: tuple(sorted(values, key=lambda c: c.timestamp))
            for key, values in option_series_lists.items()
        }
        option_timestamps = {key: [c.timestamp for c in values] for key, values in option_series.items()}

        tested_days += 1
        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        n15: list[IntradayCandle] = []
        option_history: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        last_entry_time: datetime | None = None

        for candle in day_spot:
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

            assessment = assess_strategy_b(
                n1[-24:], n5[-10:], n15[-6:], ce[-24:], pe[-24:], trade_threshold=threshold
            )
            if assessment.signal in {StrategyBSignal.WATCH_CE, StrategyBSignal.WATCH_PE}:
                watch_signals += 1
                continue
            if assessment.signal in {StrategyBSignal.BLOCKED_CE, StrategyBSignal.BLOCKED_PE}:
                blocked_signals += 1
                continue
            if assessment.signal not in {StrategyBSignal.TRADE_CE, StrategyBSignal.TRADE_PE}:
                continue
            if last_entry_time and (candle.timestamp - last_entry_time) < timedelta(minutes=cooldown_minutes):
                continue

            side = assessment.direction
            series_key = (side, strike)
            full_series = option_series.get(series_key, ())
            times = option_timestamps.get(series_key, [])
            if not full_series or not times:
                unavailable += 1
                continue
            entry_idx = bisect_right(times, candle.timestamp)
            if entry_idx >= len(full_series):
                continue
            entry_candle = full_series[entry_idx]
            if entry_candle.timestamp.date() != day_value:
                continue

            outcome, entry, exit_price, net, held = _trade_outcome(
                full_series,
                entry_idx,
                target_points=target_points,
                stop_points=stop_points,
                max_hold_minutes=max_hold_minutes,
                entry_slippage_points=entry_slippage_points,
                friction_points=friction_points,
            )
            gross = net + friction_points
            trades.append(
                StrategyBTrade(
                    signal_time=candle.timestamp,
                    entry_time=entry_candle.timestamp,
                    direction=side,
                    strike=strike,
                    score=assessment.score,
                    opposite_score=assessment.opposite_score,
                    entry_premium=round(entry, 2),
                    exit_premium=round(exit_price, 2),
                    outcome=outcome,
                    gross_points=round(gross, 2),
                    net_points=round(net, 2),
                    hold_minutes=round(held, 1),
                )
            )
            last_entry_time = entry_candle.timestamp

    return StrategyBBacktestReport(
        from_date=from_date,
        to_date=to_date,
        threshold=threshold,
        target_points=target_points,
        stop_points=stop_points,
        max_hold_minutes=max_hold_minutes,
        entry_slippage_points=entry_slippage_points,
        friction_points=friction_points,
        tested_days=tested_days,
        trades=tuple(trades),
        watch_signals=watch_signals,
        blocked_signals=blocked_signals,
        unavailable_minutes=unavailable,
        skipped_days=tuple(skipped_days),
    )
