from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

from nandi_oi.models import IntradayCandle
from test1.public_backtest import (
    _download_public_sample,
    _nearest_common_strike,
    _parse_option_frame,
    _parse_spot_frame,
    _row_to_candle,
    _update_aggregate,
)

from .engine import StrategyBSignal, assess_strategy_b


@dataclass(frozen=True)
class V2Profile:
    name: str
    threshold: float
    min_structure: float
    min_oi: float
    min_premium: float
    min_volume: float
    min_breakout: float
    min_momentum: float
    min_confirmation: float
    trigger_window_minutes: int
    trigger_buffer_points: float
    stop_pct: float
    atr_multiple: float
    min_stop_points: float
    max_stop_pct: float
    reward_risk: float
    max_hold_minutes: int
    cooldown_minutes: int
    max_trades_per_day: int
    min_option_premium: float


PROFILES: tuple[V2Profile, ...] = (
    V2Profile(
        name="CONTROLLED",
        threshold=90.0,
        min_structure=15.0,
        min_oi=12.0,
        min_premium=9.0,
        min_volume=5.0,
        min_breakout=6.0,
        min_momentum=10.0,
        min_confirmation=10.0,
        trigger_window_minutes=3,
        trigger_buffer_points=0.10,
        stop_pct=0.05,
        atr_multiple=1.5,
        min_stop_points=4.0,
        max_stop_pct=0.11,
        reward_risk=1.8,
        max_hold_minutes=20,
        cooldown_minutes=45,
        max_trades_per_day=3,
        min_option_premium=30.0,
    ),
    V2Profile(
        name="SELECTIVE",
        threshold=94.0,
        min_structure=15.0,
        min_oi=12.0,
        min_premium=9.0,
        min_volume=10.0,
        min_breakout=6.0,
        min_momentum=10.0,
        min_confirmation=10.0,
        trigger_window_minutes=3,
        trigger_buffer_points=0.10,
        stop_pct=0.055,
        atr_multiple=1.8,
        min_stop_points=4.5,
        max_stop_pct=0.12,
        reward_risk=1.9,
        max_hold_minutes=25,
        cooldown_minutes=60,
        max_trades_per_day=2,
        min_option_premium=35.0,
    ),
    V2Profile(
        name="ELITE",
        threshold=97.0,
        min_structure=17.5,
        min_oi=12.0,
        min_premium=12.0,
        min_volume=10.0,
        min_breakout=10.0,
        min_momentum=10.0,
        min_confirmation=10.0,
        trigger_window_minutes=2,
        trigger_buffer_points=0.10,
        stop_pct=0.06,
        atr_multiple=2.0,
        min_stop_points=5.0,
        max_stop_pct=0.13,
        reward_risk=2.0,
        max_hold_minutes=30,
        cooldown_minutes=75,
        max_trades_per_day=2,
        min_option_premium=40.0,
    ),
)


@dataclass(frozen=True)
class V2Trade:
    signal_time: datetime
    entry_time: datetime
    direction: str
    strike: int
    score: float
    entry: float
    stop_distance: float
    target_distance: float
    outcome: str
    net_points: float
    hold_minutes: float


@dataclass(frozen=True)
class V2Report:
    profile: str
    from_date: date
    to_date: date
    tested_days: int
    eligible_signals: int
    trigger_misses: int
    volatility_rejects: int
    premium_rejects: int
    trades: tuple[V2Trade, ...]

    @property
    def total(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(t.outcome == "WIN" for t in self.trades)

    @property
    def losses(self) -> int:
        return sum(t.outcome == "LOSS" for t in self.trades)

    @property
    def timeouts(self) -> int:
        return sum(t.outcome == "TIMEOUT" for t in self.trades)

    @property
    def win_rate(self) -> float:
        return round(100.0 * self.wins / self.total, 2) if self.total else 0.0

    @property
    def profitable_rate(self) -> float:
        return round(100.0 * sum(t.net_points > 0 for t in self.trades) / self.total, 2) if self.total else 0.0

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
            return round(gains, 2) if gains else 0.0
        return round(gains / losses, 2)

    @property
    def max_drawdown(self) -> float:
        equity = peak = drawdown = 0.0
        for trade in self.trades:
            equity += trade.net_points
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return round(drawdown, 2)

    def summary(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "tested_days": self.tested_days,
            "eligible_signals": self.eligible_signals,
            "trigger_misses": self.trigger_misses,
            "volatility_rejects": self.volatility_rejects,
            "premium_rejects": self.premium_rejects,
            "trades": self.total,
            "wins": self.wins,
            "losses": self.losses,
            "timeouts": self.timeouts,
            "target_win_rate_pct": self.win_rate,
            "profitable_trade_rate_pct": self.profitable_rate,
            "net_points_after_friction": self.net_points,
            "expectancy_points_per_trade": self.expectancy,
            "profit_factor": self.profit_factor,
            "max_drawdown_points": self.max_drawdown,
        }


def _atr(candles: tuple[IntradayCandle, ...], lookback: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    sample = candles[-min(lookback, len(candles) - 1):]
    previous = candles[-len(sample) - 1].close if len(candles) > len(sample) else candles[0].close
    values: list[float] = []
    for candle in sample:
        values.append(max(candle.high - candle.low, abs(candle.high - previous), abs(candle.low - previous)))
        previous = candle.close
    return mean(values) if values else 0.0


def _passes_components(assessment, profile: V2Profile) -> bool:
    return (
        assessment.score >= profile.threshold
        and assessment.structure_score >= profile.min_structure
        and assessment.oi_score >= profile.min_oi
        and assessment.premium_score >= profile.min_premium
        and assessment.volume_score >= profile.min_volume
        and assessment.breakout_score >= profile.min_breakout
        and assessment.momentum_score >= profile.min_momentum
        and assessment.confirmation_score >= profile.min_confirmation
        and not assessment.blockers
        and assessment.signal in {StrategyBSignal.TRADE_CE, StrategyBSignal.TRADE_PE}
    )


def _trigger_entry(
    full_series: tuple[IntradayCandle, ...],
    timestamps: list[datetime],
    signal_time: datetime,
    signal_option_candle: IntradayCandle,
    profile: V2Profile,
    entry_slippage: float,
) -> tuple[int, float] | None:
    start = bisect_right(timestamps, signal_time)
    if start >= len(full_series):
        return None
    trigger = signal_option_candle.high + profile.trigger_buffer_points
    deadline = signal_time + timedelta(minutes=profile.trigger_window_minutes)
    for index in range(start, len(full_series)):
        candle = full_series[index]
        if candle.timestamp > deadline or candle.timestamp.date() != signal_time.date():
            break
        if candle.high >= trigger:
            raw_fill = max(trigger, candle.open)
            return index, raw_fill + entry_slippage
    return None


def _evaluate_trade(
    full_series: tuple[IntradayCandle, ...],
    entry_index: int,
    entry: float,
    stop_distance: float,
    target_distance: float,
    max_hold_minutes: int,
    friction_points: float,
) -> tuple[str, float, float]:
    stop = max(0.05, entry - stop_distance)
    target = entry + target_distance
    entry_time = full_series[entry_index].timestamp
    cutoff = entry_time + timedelta(minutes=max_hold_minutes)
    future = [c for c in full_series[entry_index:] if c.timestamp <= cutoff and c.timestamp.date() == entry_time.date()]
    if not future:
        return "TIMEOUT", -friction_points, 0.0
    for candle in future:
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
        # Intrabar ordering is unknown: ambiguous target+stop bars are treated as losses.
        if stop_hit:
            held = max(0.0, (candle.timestamp - entry_time).total_seconds() / 60.0)
            return "LOSS", -stop_distance - friction_points, held
        if target_hit:
            held = max(0.0, (candle.timestamp - entry_time).total_seconds() / 60.0)
            return "WIN", target_distance - friction_points, held
    last = future[-1]
    held = max(0.0, (last.timestamp - entry_time).total_seconds() / 60.0)
    return "TIMEOUT", last.close - entry - friction_points, held


def run_v2_backtest(
    from_date: date,
    to_date: date,
    profile: V2Profile,
    *,
    cache_path: str | Path = "/tmp/shiv_strategy_b_public/nifty_1y_1min.xlsx",
    entry_slippage_points: float = 0.20,
    friction_points: float = 0.50,
) -> V2Report:
    path = _download_public_sample(Path(cache_path))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    spot_df = spot_df[(spot_df["timestamp"].dt.date >= from_date) & (spot_df["timestamp"].dt.date <= to_date)]
    opt_df = opt_df[(opt_df["day"] >= from_date) & (opt_df["day"] <= to_date)]
    if spot_df.empty or opt_df.empty:
        raise RuntimeError("No overlapping public spot/option data for the requested Strategy B v2 window")

    option_rows_by_day: dict[date, list[object]] = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows_by_day[row.day].append(row)

    spot_by_day: dict[date, tuple[IntradayCandle, ...]] = {}
    for day_value, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True):
        spot_by_day[day_value] = tuple(_row_to_candle(row) for row in group.itertuples(index=False))

    tested_days = eligible_signals = trigger_misses = volatility_rejects = premium_rejects = 0
    trades: list[V2Trade] = []

    for day_value in sorted(spot_by_day):
        spot_candles = spot_by_day[day_value]
        raw_options = option_rows_by_day.get(day_value, [])
        if len(spot_candles) < 60 or not raw_options:
            continue

        options_at_time: dict[datetime, list[tuple[str, object]]] = defaultdict(list)
        option_lists: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        strikes_by_side: dict[str, set[int]] = {"CE": set(), "PE": set()}
        for row in raw_options:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(candle)
            strikes_by_side[side].add(strike)

        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            continue

        full_series = {k: tuple(sorted(v, key=lambda c: c.timestamp)) for k, v in option_lists.items()}
        full_times = {k: [c.timestamp for c in v] for k, v in full_series.items()}
        tested_days += 1

        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        n15: list[IntradayCandle] = []
        option_history: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        last_entry: datetime | None = None
        day_trades = 0

        for spot in spot_candles:
            if day_trades >= profile.max_trades_per_day:
                break
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            _update_aggregate(n15, spot, 15)
            for side, row in options_at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))

            if len(n15) < 4:
                continue
            strike = _nearest_common_strike(strikes_by_side, spot.close)
            if strike is None:
                continue
            ce = option_history.get(("CE", strike), [])
            pe = option_history.get(("PE", strike), [])
            if min(len(ce), len(pe), len(n1), len(n5)) < 8:
                continue

            assessment = assess_strategy_b(
                n1[-30:], n5[-12:], n15[-6:], ce[-30:], pe[-30:], trade_threshold=profile.threshold
            )
            if not _passes_components(assessment, profile):
                continue
            if last_entry and spot.timestamp - last_entry < timedelta(minutes=profile.cooldown_minutes):
                continue

            eligible_signals += 1
            side = assessment.direction
            key = (side, strike)
            history = option_history[key]
            signal_option = history[-1]
            if signal_option.close < profile.min_option_premium:
                premium_rejects += 1
                continue

            triggered = _trigger_entry(
                full_series[key], full_times[key], spot.timestamp, signal_option, profile, entry_slippage_points
            )
            if triggered is None:
                trigger_misses += 1
                continue
            entry_index, entry = triggered

            pre_entry_end = entry_index
            recent = full_series[key][max(0, pre_entry_end - 20):pre_entry_end]
            option_atr = _atr(tuple(recent))
            stop_distance = max(profile.min_stop_points, entry * profile.stop_pct, option_atr * profile.atr_multiple)
            if stop_distance > entry * profile.max_stop_pct:
                volatility_rejects += 1
                continue
            target_distance = stop_distance * profile.reward_risk

            outcome, net, held = _evaluate_trade(
                full_series[key], entry_index, entry, stop_distance, target_distance,
                profile.max_hold_minutes, friction_points,
            )
            entry_time = full_series[key][entry_index].timestamp
            trades.append(
                V2Trade(
                    signal_time=spot.timestamp,
                    entry_time=entry_time,
                    direction=side,
                    strike=strike,
                    score=assessment.score,
                    entry=round(entry, 2),
                    stop_distance=round(stop_distance, 2),
                    target_distance=round(target_distance, 2),
                    outcome=outcome,
                    net_points=round(net, 2),
                    hold_minutes=round(held, 1),
                )
            )
            last_entry = entry_time
            day_trades += 1

    return V2Report(
        profile=profile.name,
        from_date=from_date,
        to_date=to_date,
        tested_days=tested_days,
        eligible_signals=eligible_signals,
        trigger_misses=trigger_misses,
        volatility_rejects=volatility_rejects,
        premium_rejects=premium_rejects,
        trades=tuple(trades),
    )
