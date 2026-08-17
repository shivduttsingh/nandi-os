from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from math import sqrt
from typing import Iterable

from nandi_oi.models import IntradayCandle, OptionLeg, OptionSnapshot

from .technical import NANDI_TOP_10_INDICATORS, TechnicalDirection, indicator_votes


@dataclass(frozen=True)
class IndicatorSignal:
    indicator: str
    available_at: datetime
    side: str
    strength: float


@dataclass(frozen=True)
class IndicatorValidationTrade:
    indicator: str
    signal_at: datetime
    opened_at: datetime
    closed_at: datetime
    side: str
    strike: float
    expiry: str
    entry_premium: float
    exit_premium: float
    stop_premium: float
    target_premium: float
    signal_strength: float
    exit_reason: str
    premium_points: float
    premium_return_pct: float


@dataclass(frozen=True)
class IndicatorValidationResult:
    indicator: str
    signals: int
    trades: tuple[IndicatorValidationTrade, ...]
    minimum_validation_trades: int

    @property
    def wins(self) -> int:
        return sum(trade.premium_points > 0 for trade in self.trades)

    @property
    def losses(self) -> int:
        return sum(trade.premium_points <= 0 for trade in self.trades)

    @property
    def win_rate(self) -> float:
        return self.wins / len(self.trades) * 100.0 if self.trades else 0.0

    @property
    def net_points(self) -> float:
        return round(sum(trade.premium_points for trade in self.trades), 2)

    @property
    def average_return_pct(self) -> float:
        return (
            round(sum(trade.premium_return_pct for trade in self.trades) / len(self.trades), 2)
            if self.trades else 0.0
        )

    @property
    def max_drawdown(self) -> float:
        equity = peak = drawdown = 0.0
        for trade in self.trades:
            equity += trade.premium_points
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return round(drawdown, 2)

    @property
    def confidence_interval(self) -> tuple[float, float]:
        """Wilson 95% interval for the observed win rate."""
        count = len(self.trades)
        if not count:
            return 0.0, 0.0
        z = 1.96
        proportion = self.wins / count
        denominator = 1.0 + z * z / count
        centre = (proportion + z * z / (2.0 * count)) / denominator
        margin = z * sqrt(
            proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count)
        ) / denominator
        return round(max(0.0, centre - margin) * 100.0, 1), round(
            min(1.0, centre + margin) * 100.0, 1,
        )

    @property
    def validation_status(self) -> str:
        count = len(self.trades)
        return (
            "VALIDATED SAMPLE"
            if count >= self.minimum_validation_trades
            else f"UNVALIDATED {count}/{self.minimum_validation_trades}"
        )

    def summary_row(self) -> dict[str, object]:
        low, high = self.confidence_interval
        return {
            "Indicator": self.indicator,
            "Directional transitions": self.signals,
            "Completed trades": len(self.trades),
            "Wins": self.wins,
            "Losses": self.losses,
            "Win rate %": round(self.win_rate, 1),
            "95% interval": f"{low:.1f}%–{high:.1f}%" if self.trades else "—",
            "Average premium return %": self.average_return_pct,
            "Net premium points": self.net_points,
            "Maximum drawdown": self.max_drawdown,
            "Status": self.validation_status,
        }


@dataclass(frozen=True)
class IndicatorValidationReport:
    start_date: date
    end_date: date
    signal_interval_minutes: int
    source_candles: int
    option_snapshots: int
    results: tuple[IndicatorValidationResult, ...]

    def summary_rows(self) -> list[dict[str, object]]:
        by_name = {result.indicator: result for result in self.results}
        return [by_name[name].summary_row() for name in NANDI_TOP_10_INDICATORS]

    def ledger_rows(self, indicator: str | None = None) -> list[dict[str, object]]:
        rows = [
            asdict(trade)
            for result in self.results
            if indicator is None or result.indicator == indicator
            for trade in result.trades
        ]
        return sorted(rows, key=lambda row: (row["opened_at"], row["indicator"]))


def aggregate_candles(
    candles: Iterable[IntradayCandle],
    interval_minutes: int = 15,
) -> tuple[IntradayCandle, ...]:
    """Aggregate complete regular-session candles without crossing trading days."""
    if interval_minutes < 5 or interval_minutes % 5:
        raise ValueError("Indicator validation interval must be a multiple of five minutes")
    required = interval_minutes // 5
    session_start = 9 * 60 + 15
    groups: dict[tuple[date, int], list[IntradayCandle]] = {}
    for candle in sorted(candles, key=lambda item: item.timestamp):
        minute = candle.timestamp.hour * 60 + candle.timestamp.minute
        offset = minute - session_start
        if offset < 0 or minute >= 15 * 60 + 30:
            continue
        bucket = offset // interval_minutes
        groups.setdefault((candle.timestamp.date(), bucket), []).append(candle)

    output: list[IntradayCandle] = []
    for _, bars in sorted(groups.items()):
        bars = sorted(bars, key=lambda item: item.timestamp)
        if len(bars) != required:
            continue
        expected = [bars[0].timestamp + timedelta(minutes=5 * index) for index in range(required)]
        if [bar.timestamp for bar in bars] != expected:
            continue
        output.append(
            IntradayCandle(
                timestamp=bars[0].timestamp,
                open=bars[0].open,
                high=max(bar.high for bar in bars),
                low=min(bar.low for bar in bars),
                close=bars[-1].close,
                volume=sum(bar.volume for bar in bars),
                open_interest=bars[-1].open_interest,
            )
        )
    return tuple(output)


class IndividualIndicatorBacktester:
    """Replay each Top 10 indicator independently against actual ATM option OHLC."""

    def __init__(
        self,
        *,
        signal_interval_minutes: int = 15,
        stop_pct: float = 0.05,
        target_pct: float = 0.075,
        maximum_hold_minutes: int = 45,
        maximum_trades_daily: int = 3,
        slippage_pct: float = 0.0025,
        minimum_validation_trades: int = 100,
    ) -> None:
        if signal_interval_minutes < 5 or signal_interval_minutes % 5:
            raise ValueError("Signal interval must be a multiple of five minutes")
        if not 0 < stop_pct < target_pct < 1:
            raise ValueError("Stop and target percentages are invalid")
        if maximum_hold_minutes < signal_interval_minutes:
            raise ValueError("Maximum hold must be at least one signal interval")
        if maximum_trades_daily < 1 or minimum_validation_trades < 1:
            raise ValueError("Trade and validation counts must be positive")
        if not 0 <= slippage_pct < 0.05:
            raise ValueError("Slippage percentage is invalid")
        self.signal_interval_minutes = signal_interval_minutes
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.maximum_hold_minutes = maximum_hold_minutes
        self.maximum_trades_daily = maximum_trades_daily
        self.slippage_pct = slippage_pct
        self.minimum_validation_trades = minimum_validation_trades

    def _signals(self, candles: Iterable[IntradayCandle]) -> dict[str, tuple[IndicatorSignal, ...]]:
        bars = aggregate_candles(candles, self.signal_interval_minutes)
        history: list[IntradayCandle] = []
        previous = {name: TechnicalDirection.UNAVAILABLE for name in NANDI_TOP_10_INDICATORS}
        signals: dict[str, list[IndicatorSignal]] = {name: [] for name in NANDI_TOP_10_INDICATORS}
        for bar in bars:
            history.append(bar)
            votes = {vote.name: vote for vote in indicator_votes(history)}
            available_at = bar.timestamp + timedelta(minutes=self.signal_interval_minutes)
            for name in NANDI_TOP_10_INDICATORS:
                vote = votes[name]
                if (
                    vote.direction in {TechnicalDirection.BULLISH, TechnicalDirection.BEARISH}
                    and vote.direction != previous[name]
                ):
                    signals[name].append(
                        IndicatorSignal(
                            indicator=name,
                            available_at=available_at,
                            side="CE" if vote.direction == TechnicalDirection.BULLISH else "PE",
                            strength=round(vote.strength * 100.0, 1),
                        )
                    )
                previous[name] = vote.direction
        return {name: tuple(values) for name, values in signals.items()}

    @staticmethod
    def _atm_leg(snapshot: OptionSnapshot, side: str) -> OptionLeg | None:
        candidates = [leg for leg in snapshot.legs if leg.side == side and (leg.open_price or leg.ltp) > 0]
        return min(candidates, key=lambda leg: abs(leg.strike - snapshot.spot)) if candidates else None

    @staticmethod
    def _contract_leg(snapshot: OptionSnapshot, side: str, strike: float) -> OptionLeg | None:
        return next(
            (leg for leg in snapshot.legs if leg.side == side and leg.strike == strike),
            None,
        )

    def _run_indicator(
        self,
        indicator: str,
        signals: tuple[IndicatorSignal, ...],
        snapshots: tuple[OptionSnapshot, ...],
    ) -> IndicatorValidationResult:
        trades: list[IndicatorValidationTrade] = []
        trades_by_day: dict[date, int] = {}
        signal_index = 0
        open_trade: dict[str, object] | None = None
        previous_snapshot: OptionSnapshot | None = None

        def close(snapshot: OptionSnapshot, raw_price: float, reason: str) -> None:
            nonlocal open_trade
            assert open_trade is not None
            exit_price = round(max(0.01, raw_price * (1.0 - self.slippage_pct)), 2)
            entry = float(open_trade["entry_premium"])
            points = round(exit_price - entry, 2)
            trades.append(
                IndicatorValidationTrade(
                    indicator=indicator,
                    signal_at=open_trade["signal_at"],
                    opened_at=open_trade["opened_at"],
                    closed_at=snapshot.timestamp,
                    side=str(open_trade["side"]),
                    strike=float(open_trade["strike"]),
                    expiry=str(open_trade["expiry"]),
                    entry_premium=entry,
                    exit_premium=exit_price,
                    stop_premium=float(open_trade["stop_premium"]),
                    target_premium=float(open_trade["target_premium"]),
                    signal_strength=float(open_trade["signal_strength"]),
                    exit_reason=reason,
                    premium_points=points,
                    premium_return_pct=round(points / entry * 100.0, 2),
                )
            )
            open_trade = None

        for snapshot in snapshots:
            if open_trade and previous_snapshot and previous_snapshot.timestamp.date() != snapshot.timestamp.date():
                side = str(open_trade["side"])
                strike = float(open_trade["strike"])
                previous_leg = self._contract_leg(previous_snapshot, side, strike)
                close(previous_snapshot, previous_leg.ltp if previous_leg else float(open_trade["entry_premium"]), "End of day")

            if open_trade:
                side = str(open_trade["side"])
                strike = float(open_trade["strike"])
                leg = self._contract_leg(snapshot, side, strike)
                if leg:
                    low = leg.low_price or leg.ltp
                    high = leg.high_price or leg.ltp
                    stop = float(open_trade["stop_premium"])
                    target = float(open_trade["target_premium"])
                    if low <= stop:
                        close(snapshot, stop, "5% stop")
                    elif high >= target:
                        close(snapshot, target, "7.5% target")
                    elif snapshot.timestamp >= open_trade["opened_at"] + timedelta(minutes=self.maximum_hold_minutes):
                        close(snapshot, leg.ltp, f"{self.maximum_hold_minutes}-minute exit")

            current_signals: list[IndicatorSignal] = []
            while signal_index < len(signals) and signals[signal_index].available_at <= snapshot.timestamp:
                current_signals.append(signals[signal_index])
                signal_index += 1
            if not open_trade and current_signals:
                signal = current_signals[-1]
                day = snapshot.timestamp.date()
                is_fresh = (
                    signal.available_at.date() == day
                    and snapshot.timestamp - signal.available_at <= timedelta(minutes=5)
                )
                if is_fresh and trades_by_day.get(day, 0) < self.maximum_trades_daily:
                    leg = self._atm_leg(snapshot, signal.side)
                    if leg:
                        raw_entry = leg.open_price or leg.ltp
                        entry = round(raw_entry * (1.0 + self.slippage_pct), 2)
                        open_trade = {
                            "signal_at": signal.available_at,
                            "opened_at": snapshot.timestamp,
                            "side": signal.side,
                            "strike": leg.strike,
                            "expiry": snapshot.expiry,
                            "entry_premium": entry,
                            "stop_premium": round(entry * (1.0 - self.stop_pct), 2),
                            "target_premium": round(entry * (1.0 + self.target_pct), 2),
                            "signal_strength": signal.strength,
                        }
                        trades_by_day[day] = trades_by_day.get(day, 0) + 1
                        # The signal is known before this five-minute option candle opens.
                        low = leg.low_price or leg.ltp
                        high = leg.high_price or leg.ltp
                        if low <= float(open_trade["stop_premium"]):
                            close(snapshot, float(open_trade["stop_premium"]), "5% stop")
                        elif high >= float(open_trade["target_premium"]):
                            close(snapshot, float(open_trade["target_premium"]), "7.5% target")
            previous_snapshot = snapshot

        if open_trade and previous_snapshot:
            side = str(open_trade["side"])
            strike = float(open_trade["strike"])
            leg = self._contract_leg(previous_snapshot, side, strike)
            close(previous_snapshot, leg.ltp if leg else float(open_trade["entry_premium"]), "End of test")

        return IndicatorValidationResult(
            indicator=indicator,
            signals=len(signals),
            trades=tuple(trades),
            minimum_validation_trades=self.minimum_validation_trades,
        )

    def run(
        self,
        nifty_five_minute_candles: Iterable[IntradayCandle],
        weekly_option_snapshots: Iterable[OptionSnapshot],
    ) -> IndicatorValidationReport:
        candles = tuple(sorted(nifty_five_minute_candles, key=lambda item: item.timestamp))
        snapshots = tuple(sorted(weekly_option_snapshots, key=lambda item: item.timestamp))
        if not candles:
            raise ValueError("No historical NIFTY candles were available")
        if not snapshots:
            raise ValueError("No historical weekly option snapshots were available")
        signals = self._signals(candles)
        results = tuple(
            self._run_indicator(name, signals[name], snapshots)
            for name in NANDI_TOP_10_INDICATORS
        )
        return IndicatorValidationReport(
            start_date=max(candles[0].timestamp.date(), snapshots[0].timestamp.date()),
            end_date=min(candles[-1].timestamp.date(), snapshots[-1].timestamp.date()),
            signal_interval_minutes=self.signal_interval_minutes,
            source_candles=len(candles),
            option_snapshots=len(snapshots),
            results=results,
        )
