from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

from nandi_oi.models import IntradayCandle
from test1.public_backtest import (
    _download_public_sample,
    _nearest_common_strike,
    _parse_option_frame,
    _parse_spot_frame,
    _row_to_candle,
    _update_aggregate,
)


@dataclass(frozen=True)
class ORBProfile:
    name: str
    opening_range_minutes: int
    trade_start: time
    trade_end: time
    retest_atr_tolerance: float
    max_extension_atr: float
    min_option_volume_ratio: float
    min_option_outperformance_pct: float
    trigger_window_minutes: int
    option_atr_stop_multiple: float
    min_stop_points: float
    max_stop_pct: float
    reward_risk: float
    max_hold_minutes: int
    min_option_premium: float


PROFILES: tuple[ORBProfile, ...] = (
    ORBProfile(
        name="ORB15_CORE",
        opening_range_minutes=15,
        trade_start=time(9, 35),
        trade_end=time(11, 30),
        retest_atr_tolerance=0.35,
        max_extension_atr=1.35,
        min_option_volume_ratio=1.00,
        min_option_outperformance_pct=0.75,
        trigger_window_minutes=2,
        option_atr_stop_multiple=1.25,
        min_stop_points=4.0,
        max_stop_pct=0.11,
        reward_risk=1.50,
        max_hold_minutes=30,
        min_option_premium=35.0,
    ),
    ORBProfile(
        name="ORB15_STRICT",
        opening_range_minutes=15,
        trade_start=time(9, 35),
        trade_end=time(11, 15),
        retest_atr_tolerance=0.25,
        max_extension_atr=1.10,
        min_option_volume_ratio=1.20,
        min_option_outperformance_pct=1.00,
        trigger_window_minutes=2,
        option_atr_stop_multiple=1.35,
        min_stop_points=4.5,
        max_stop_pct=0.10,
        reward_risk=1.60,
        max_hold_minutes=30,
        min_option_premium=40.0,
    ),
    ORBProfile(
        name="ORB30_CORE",
        opening_range_minutes=30,
        trade_start=time(9, 50),
        trade_end=time(12, 15),
        retest_atr_tolerance=0.30,
        max_extension_atr=1.25,
        min_option_volume_ratio=1.05,
        min_option_outperformance_pct=0.75,
        trigger_window_minutes=2,
        option_atr_stop_multiple=1.30,
        min_stop_points=4.0,
        max_stop_pct=0.11,
        reward_risk=1.50,
        max_hold_minutes=30,
        min_option_premium=35.0,
    ),
)


@dataclass(frozen=True)
class ORBTrade:
    signal_time: datetime
    entry_time: datetime
    direction: str
    strike: int
    opening_range_high: float
    opening_range_low: float
    entry: float
    stop_distance: float
    target_distance: float
    option_volume_ratio: float
    chosen_move_pct: float
    opposite_move_pct: float
    outcome: str
    net_points: float
    hold_minutes: float


@dataclass(frozen=True)
class ORBReport:
    profile: str
    from_date: date
    to_date: date
    tested_days: int
    qualified_setups: int
    trigger_misses: int
    risk_rejects: int
    premium_rejects: int
    trades: tuple[ORBTrade, ...]

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
    def target_win_rate(self) -> float:
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
        equity = peak = max_dd = 0.0
        for trade in self.trades:
            equity += trade.net_points
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return round(max_dd, 2)

    def summary(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "tested_days": self.tested_days,
            "qualified_setups": self.qualified_setups,
            "trigger_misses": self.trigger_misses,
            "risk_rejects": self.risk_rejects,
            "premium_rejects": self.premium_rejects,
            "trades": self.total,
            "wins": self.wins,
            "losses": self.losses,
            "timeouts": self.timeouts,
            "target_win_rate_pct": self.target_win_rate,
            "profitable_trade_rate_pct": self.profitable_rate,
            "net_points_after_friction": self.net_points,
            "expectancy_points_per_trade": self.expectancy,
            "profit_factor": self.profit_factor,
            "max_drawdown_points": self.max_drawdown,
        }


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


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


def _pct(start: float, end: float) -> float:
    return ((end / start) - 1.0) * 100.0 if start > 0 else 0.0


def _move(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    return _pct(candles[-lookback - 1].close, candles[-1].close)


def _oi_change(candles: list[IntradayCandle], lookback: int = 3) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    old = candles[-lookback - 1].open_interest
    new = candles[-1].open_interest
    return _pct(old, new) if old > 0 else 0.0


def _volume_ratio(candles: list[IntradayCandle], lookback: int = 12) -> float:
    if len(candles) < 4:
        return 0.0
    history = [c.volume for c in candles[-lookback - 1:-1] if c.volume > 0]
    if not history:
        return 0.0
    base = median(history)
    return candles[-1].volume / base if base > 0 else 0.0


def _opening_range(day_spot: tuple[IntradayCandle, ...], minutes: int) -> tuple[float, float] | None:
    start = time(9, 15)
    end_dt = datetime.combine(day_spot[0].timestamp.date(), start) + timedelta(minutes=minutes)
    range_candles = [c for c in day_spot if c.timestamp.time() >= start and c.timestamp < end_dt]
    if len(range_candles) < max(10, minutes - 2):
        return None
    return max(c.high for c in range_candles), min(c.low for c in range_candles)


def _spot_signal(
    n1: list[IntradayCandle],
    n5: list[IntradayCandle],
    or_high: float,
    or_low: float,
    profile: ORBProfile,
) -> str | None:
    if len(n1) < 20 or len(n5) < 4:
        return None
    current = n1[-1]
    previous = n1[-2]
    spot_atr = _atr(tuple(n1[-20:]))
    if spot_atr <= 0:
        return None
    tolerance = profile.retest_atr_tolerance * spot_atr
    recent = n1[-10:-1]
    recent_closes = [c.close for c in recent]
    five = n5[-3:]

    bullish_break_seen = any(close > or_high for close in recent_closes)
    bearish_break_seen = any(close < or_low for close in recent_closes)

    bullish_retest = (
        bullish_break_seen
        and current.low <= or_high + tolerance
        and current.close > or_high
        and current.close > current.open
        and current.close > previous.close
        and current.high > previous.high
        and 0.0 <= current.close - or_high <= profile.max_extension_atr * spot_atr
    )
    bullish_trend = five[-1].close > five[-2].close > five[-3].close and five[-1].close > or_high

    bearish_retest = (
        bearish_break_seen
        and current.high >= or_low - tolerance
        and current.close < or_low
        and current.close < current.open
        and current.close < previous.close
        and current.low < previous.low
        and 0.0 <= or_low - current.close <= profile.max_extension_atr * spot_atr
    )
    bearish_trend = five[-1].close < five[-2].close < five[-3].close and five[-1].close < or_low

    if bullish_retest and bullish_trend:
        return "CE"
    if bearish_retest and bearish_trend:
        return "PE"
    return None


def _option_confirm(
    chosen: list[IntradayCandle],
    opposite: list[IntradayCandle],
    profile: ORBProfile,
) -> tuple[bool, float, float, float]:
    if len(chosen) < 15 or len(opposite) < 5:
        return False, 0.0, 0.0, 0.0
    closes = [c.close for c in chosen[-20:]]
    ema5 = _ema(closes, 5)
    ema13 = _ema(closes, 13)
    chosen_move = _move(chosen, 3)
    opposite_move = _move(opposite, 3)
    volume_ratio = _volume_ratio(chosen)
    chosen_oi = _oi_change(chosen, 3)
    outperformance = chosen_move - opposite_move
    confirmed = (
        chosen[-1].close > ema5 > ema13
        and chosen[-1].close > chosen[-2].close
        and chosen_move > 0
        and outperformance >= profile.min_option_outperformance_pct
        and volume_ratio >= profile.min_option_volume_ratio
        and chosen_oi > -12.0
    )
    return confirmed, volume_ratio, chosen_move, opposite_move


def _trigger_entry(
    series: tuple[IntradayCandle, ...],
    timestamps: list[datetime],
    signal_time: datetime,
    signal_option: IntradayCandle,
    profile: ORBProfile,
    slippage: float,
) -> tuple[int, float] | None:
    start = bisect_right(timestamps, signal_time)
    trigger = signal_option.high + 0.10
    deadline = signal_time + timedelta(minutes=profile.trigger_window_minutes)
    for index in range(start, len(series)):
        candle = series[index]
        if candle.timestamp.date() != signal_time.date() or candle.timestamp > deadline:
            break
        if candle.high >= trigger:
            return index, max(candle.open, trigger) + slippage
    return None


def _evaluate(
    series: tuple[IntradayCandle, ...],
    entry_index: int,
    entry: float,
    stop_distance: float,
    target_distance: float,
    hold_minutes: int,
    friction: float,
) -> tuple[str, float, float]:
    stop = max(0.05, entry - stop_distance)
    target = entry + target_distance
    entry_time = series[entry_index].timestamp
    cutoff = entry_time + timedelta(minutes=hold_minutes)
    future = [c for c in series[entry_index:] if c.timestamp.date() == entry_time.date() and c.timestamp <= cutoff]
    if not future:
        return "TIMEOUT", -friction, 0.0
    for candle in future:
        stop_hit = candle.low <= stop
        target_hit = candle.high >= target
        if stop_hit:
            held = max(0.0, (candle.timestamp - entry_time).total_seconds() / 60.0)
            return "LOSS", -stop_distance - friction, held
        if target_hit:
            held = max(0.0, (candle.timestamp - entry_time).total_seconds() / 60.0)
            return "WIN", target_distance - friction, held
    last = future[-1]
    held = max(0.0, (last.timestamp - entry_time).total_seconds() / 60.0)
    return "TIMEOUT", last.close - entry - friction, held


def run_orb_backtest(
    from_date: date,
    to_date: date,
    profile: ORBProfile,
    *,
    cache_path: str | Path = "/tmp/shiv_strategy_b_public/nifty_1y_1min.xlsx",
    entry_slippage_points: float = 0.20,
    friction_points: float = 0.50,
) -> ORBReport:
    path = _download_public_sample(Path(cache_path))
    spot_df = _parse_spot_frame(path)
    opt_df = _parse_option_frame(path)
    spot_df = spot_df[(spot_df["timestamp"].dt.date >= from_date) & (spot_df["timestamp"].dt.date <= to_date)]
    opt_df = opt_df[(opt_df["day"] >= from_date) & (opt_df["day"] <= to_date)]
    if spot_df.empty or opt_df.empty:
        raise RuntimeError("No overlapping public NIFTY spot/option data for the Strategy B v3 window")

    option_rows_by_day: dict[date, list[object]] = defaultdict(list)
    for row in opt_df.itertuples(index=False):
        option_rows_by_day[row.day].append(row)

    spot_by_day: dict[date, tuple[IntradayCandle, ...]] = {}
    for day_value, group in spot_df.groupby(spot_df["timestamp"].dt.date, sort=True):
        spot_by_day[day_value] = tuple(_row_to_candle(row) for row in group.itertuples(index=False))

    tested_days = qualified_setups = trigger_misses = risk_rejects = premium_rejects = 0
    trades: list[ORBTrade] = []

    for day_value in sorted(spot_by_day):
        day_spot = spot_by_day[day_value]
        opening_range = _opening_range(day_spot, profile.opening_range_minutes)
        raw_options = option_rows_by_day.get(day_value, [])
        if opening_range is None or not raw_options:
            continue
        or_high, or_low = opening_range

        options_at_time: dict[datetime, list[tuple[str, object]]] = defaultdict(list)
        option_lists: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        strikes_by_side: dict[str, set[int]] = {"CE": set(), "PE": set()}
        for row in raw_options:
            side = "CE" if row.option_type in {"CE", "CALL"} else "PE" if row.option_type in {"PE", "PUT"} else ""
            if not side:
                continue
            strike = int(row.strike)
            option_candle = _row_to_candle(row)
            options_at_time[row.timestamp].append((side, row))
            option_lists[(side, strike)].append(option_candle)
            strikes_by_side[side].add(strike)
        if not (strikes_by_side["CE"] & strikes_by_side["PE"]):
            continue

        full_series = {k: tuple(sorted(v, key=lambda c: c.timestamp)) for k, v in option_lists.items()}
        full_times = {k: [c.timestamp for c in v] for k, v in full_series.items()}
        option_history: dict[tuple[str, int], list[IntradayCandle]] = defaultdict(list)
        n1: list[IntradayCandle] = []
        n5: list[IntradayCandle] = []
        tested_days += 1
        traded = False

        for spot in day_spot:
            n1.append(spot)
            _update_aggregate(n5, spot, 5)
            for side, row in options_at_time.get(spot.timestamp, []):
                option_history[(side, int(row.strike))].append(_row_to_candle(row))

            if traded or not (profile.trade_start <= spot.timestamp.time() <= profile.trade_end):
                continue
            direction = _spot_signal(n1, n5, or_high, or_low, profile)
            if direction is None:
                continue
            strike = _nearest_common_strike(strikes_by_side, spot.close)
            if strike is None:
                continue
            chosen = option_history.get((direction, strike), [])
            opposite_side = "PE" if direction == "CE" else "CE"
            opposite = option_history.get((opposite_side, strike), [])
            if len(chosen) < 15 or len(opposite) < 5:
                continue
            if chosen[-1].close < profile.min_option_premium:
                premium_rejects += 1
                continue

            confirmed, volume_ratio, chosen_move, opposite_move = _option_confirm(chosen, opposite, profile)
            if not confirmed:
                continue
            qualified_setups += 1

            key = (direction, strike)
            triggered = _trigger_entry(
                full_series[key], full_times[key], spot.timestamp, chosen[-1], profile, entry_slippage_points
            )
            if triggered is None:
                trigger_misses += 1
                continue
            entry_index, entry = triggered

            prior = full_series[key][max(0, entry_index - 18):entry_index]
            option_atr = _atr(tuple(prior))
            recent_lows = [c.low for c in prior[-4:]]
            swing_distance = entry - min(recent_lows) if recent_lows else 0.0
            stop_distance = max(profile.min_stop_points, option_atr * profile.option_atr_stop_multiple, swing_distance)
            if stop_distance <= 0 or stop_distance > entry * profile.max_stop_pct:
                risk_rejects += 1
                continue
            target_distance = stop_distance * profile.reward_risk

            outcome, net, held = _evaluate(
                full_series[key], entry_index, entry, stop_distance, target_distance,
                profile.max_hold_minutes, friction_points,
            )
            entry_time = full_series[key][entry_index].timestamp
            trades.append(
                ORBTrade(
                    signal_time=spot.timestamp,
                    entry_time=entry_time,
                    direction=direction,
                    strike=strike,
                    opening_range_high=round(or_high, 2),
                    opening_range_low=round(or_low, 2),
                    entry=round(entry, 2),
                    stop_distance=round(stop_distance, 2),
                    target_distance=round(target_distance, 2),
                    option_volume_ratio=round(volume_ratio, 2),
                    chosen_move_pct=round(chosen_move, 2),
                    opposite_move_pct=round(opposite_move, 2),
                    outcome=outcome,
                    net_points=round(net, 2),
                    hold_minutes=round(held, 1),
                )
            )
            traded = True

    return ORBReport(
        profile=profile.name,
        from_date=from_date,
        to_date=to_date,
        tested_days=tested_days,
        qualified_setups=qualified_setups,
        trigger_misses=trigger_misses,
        risk_rejects=risk_rejects,
        premium_rejects=premium_rejects,
        trades=tuple(trades),
    )
