from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from nandi_oi.models import IntradayCandle
from nandi_oi.upstox import UpstoxOptionChainClient
from .continuation import Test1Signal, assess_test1_continuation


@dataclass(frozen=True)
class BacktestTrade:
    timestamp: datetime
    direction: str
    score: float
    entry: float
    mfe_points: float
    mae_points: float
    move_5m: float
    move_10m: float
    move_15m: float


@dataclass(frozen=True)
class BacktestReport:
    from_date: date
    to_date: date
    trades: tuple[BacktestTrade, ...]
    late_skips: int
    prepare_signals: int

    @property
    def total(self) -> int:
        return len(self.trades)

    def hit_rate(self, points: float) -> float:
        if not self.trades:
            return 0.0
        return 100.0 * sum(t.mfe_points >= points for t in self.trades) / len(self.trades)

    def continuation_rate(self, minutes: int) -> float:
        attr = {5: 'move_5m', 10: 'move_10m', 15: 'move_15m'}[minutes]
        if not self.trades:
            return 0.0
        return 100.0 * sum(getattr(t, attr) > 0 for t in self.trades) / len(self.trades)


def _at_or_before(candles: tuple[IntradayCandle, ...], ts: datetime) -> tuple[IntradayCandle, ...]:
    return tuple(c for c in candles if c.timestamp <= ts)


def _future_move(n1: tuple[IntradayCandle, ...], idx: int, direction: str, minutes: int) -> float:
    if idx + minutes >= len(n1):
        return 0.0
    entry = n1[idx].close
    end = n1[idx + minutes].close
    return end - entry if direction == 'CE' else entry - end


def _excursions(n1: tuple[IntradayCandle, ...], idx: int, direction: str, horizon: int = 15) -> tuple[float, float]:
    entry = n1[idx].close
    future = n1[idx + 1:min(len(n1), idx + horizon + 1)]
    if not future:
        return 0.0, 0.0
    if direction == 'CE':
        return max(c.high - entry for c in future), max(entry - c.low for c in future)
    return max(entry - c.low for c in future), max(c.high - entry for c in future)


def run_test1_backtest(
    client: UpstoxOptionChainClient,
    from_date: date,
    to_date: date,
    ce_instrument_key: str,
    pe_instrument_key: str,
) -> BacktestReport:
    """Strict candle replay. Caller supplies exact option-contract keys for the test window.

    This intentionally lives outside the live app path. It never places orders and never
    mutates TEST 1 production state. For expiry-spanning studies, run one segment per exact
    CE/PE contract pair and aggregate reports; do not silently substitute today's ATM pair.
    """
    n1 = client.fetch_historical_candles(from_date, to_date, 1)
    n5 = client.fetch_historical_candles(from_date, to_date, 5)
    n15 = client.fetch_historical_candles(from_date, to_date, 15)

    def fetch_option(key: str, interval: int = 1) -> tuple[IntradayCandle, ...]:
        original = client.instrument_key
        try:
            client.instrument_key = key
            return client.fetch_historical_candles(from_date, to_date, interval)
        finally:
            client.instrument_key = original

    ce = fetch_option(ce_instrument_key)
    pe = fetch_option(pe_instrument_key)
    trades: list[BacktestTrade] = []
    late_skips = 0
    prepares = 0
    last_signal_ts: datetime | None = None

    for idx, candle in enumerate(n1):
        if idx < 15 or idx + 15 >= len(n1):
            continue
        a = assess_test1_continuation(
            _at_or_before(n1, candle.timestamp),
            _at_or_before(n5, candle.timestamp),
            _at_or_before(n15, candle.timestamp),
            _at_or_before(ce, candle.timestamp),
            _at_or_before(pe, candle.timestamp),
        )
        if a.signal in (Test1Signal.PREPARE_CE, Test1Signal.PREPARE_PE):
            prepares += 1
        if a.signal in (Test1Signal.LATE_SKIP_CE, Test1Signal.LATE_SKIP_PE):
            late_skips += 1
        if a.signal not in (Test1Signal.CONFIRMED_CE, Test1Signal.CONFIRMED_PE):
            continue
        # Count one confirmation per 5-minute cluster rather than every repeated 1m state.
        if last_signal_ts and (candle.timestamp - last_signal_ts) < timedelta(minutes=5):
            continue
        last_signal_ts = candle.timestamp
        mfe, mae = _excursions(n1, idx, a.direction)
        trades.append(BacktestTrade(
            timestamp=candle.timestamp,
            direction=a.direction,
            score=a.score,
            entry=candle.close,
            mfe_points=round(mfe, 2),
            mae_points=round(mae, 2),
            move_5m=round(_future_move(n1, idx, a.direction, 5), 2),
            move_10m=round(_future_move(n1, idx, a.direction, 10), 2),
            move_15m=round(_future_move(n1, idx, a.direction, 15), 2),
        ))

    return BacktestReport(from_date, to_date, tuple(trades), late_skips, prepares)
