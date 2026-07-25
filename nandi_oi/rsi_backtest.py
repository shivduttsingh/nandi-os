from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, Mapping

from .backtest import BacktestResult, BacktestTrade
from .models import OptionSnapshot


TIMEFRAMES = (1, 2, 3, 5, 10, 15, 30, 60)


def wilder_rsi(values: list[float], length: int = 14) -> list[float | None]:
    """TradingView-style Wilder/RMA RSI with no future-data access."""
    if length < 2:
        raise ValueError("RSI length must be at least 2")
    result: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return result
    changes = [current - previous for previous, current in zip(values, values[1:])]
    average_gain = sum(max(change, 0.0) for change in changes[:length]) / length
    average_loss = sum(max(-change, 0.0) for change in changes[:length]) / length

    def value() -> float:
        if average_loss == 0:
            return 100.0
        if average_gain == 0:
            return 0.0
        relative_strength = average_gain / average_loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    result[length] = value()
    for index in range(length, len(changes)):
        gain = max(changes[index], 0.0)
        loss = max(-changes[index], 0.0)
        average_gain = ((average_gain * (length - 1)) + gain) / length
        average_loss = ((average_loss * (length - 1)) + loss) / length
        result[index + 1] = value()
    return result


@dataclass(frozen=True)
class RsiTouch:
    timestamp: datetime
    timeframe_minutes: int
    side: str
    rsi: float
    close: float

    def row(self) -> dict[str, object]:
        row = asdict(self)
        row["timestamp"] = self.timestamp.isoformat(sep=" ", timespec="minutes")
        row["zone"] = f"Lower touch (≤)" if self.side == "LOWER" else "Upper touch (≥)"
        return row


@dataclass(frozen=True)
class RsiTimeframeSummary:
    timeframe_minutes: int
    lower_touches: int
    upper_touches: int
    lower_zone_candles: int
    upper_zone_candles: int
    candles_checked: int

    @property
    def total_touches(self) -> int:
        return self.lower_touches + self.upper_touches

    def row(self) -> dict[str, int | str]:
        label = "1 hour" if self.timeframe_minutes == 60 else f"{self.timeframe_minutes} min"
        return {
            "Timeframe": label,
            "Lower touches": self.lower_touches,
            "Upper touches": self.upper_touches,
            "Total touches": self.total_touches,
            "Candles in lower zone": self.lower_zone_candles,
            "Candles in upper zone": self.upper_zone_candles,
            "Candles checked": self.candles_checked,
        }


@dataclass(frozen=True)
class RsiTouchResult:
    start_date: date
    end_date: date
    length: int
    lower: float
    upper: float
    summaries: tuple[RsiTimeframeSummary, ...]
    touches: tuple[RsiTouch, ...]

    def summary_rows(self) -> list[dict[str, int | str]]:
        return [item.row() for item in self.summaries]

    def touch_rows(self) -> list[dict[str, object]]:
        return [item.row() for item in self.touches]


def _resample_closes(
    one_minute_closes: Mapping[datetime, float], timeframe: int,
) -> list[tuple[datetime, float]]:
    """Build intraday candles aligned to the NSE 09:15 open, using each bucket's last close."""
    buckets: dict[tuple[date, int], tuple[datetime, float]] = {}
    for timestamp, close in sorted(one_minute_closes.items()):
        market_open_minutes = 9 * 60 + 15
        minute_of_day = timestamp.hour * 60 + timestamp.minute
        offset = max(0, minute_of_day - market_open_minutes)
        bucket = offset // timeframe
        buckets[(timestamp.date(), bucket)] = (timestamp, float(close))
    return [buckets[key] for key in sorted(buckets)]


class RsiTouchAnalyzer:
    """Count independent RSI zone entries across several NIFTY timeframes."""

    def __init__(
        self,
        length: int = 14,
        lower: float = 24,
        upper: float = 72,
        timeframes: Iterable[int] = TIMEFRAMES,
    ) -> None:
        if length < 2 or length > 100:
            raise ValueError("RSI period must be between 2 and 100")
        if not 0 <= lower < upper <= 100:
            raise ValueError("RSI lower level must be below the upper level")
        selected = tuple(dict.fromkeys(int(value) for value in timeframes))
        if not selected or any(value not in TIMEFRAMES for value in selected):
            raise ValueError("Select at least one supported timeframe")
        self.length = length
        self.lower = float(lower)
        self.upper = float(upper)
        self.timeframes = selected

    def run(
        self,
        one_minute_closes: Mapping[datetime, float],
        start: date,
        end: date,
    ) -> RsiTouchResult:
        if not one_minute_closes:
            raise ValueError("No historical NIFTY candles were available")
        if start > end:
            raise ValueError("Start date must be on or before end date")

        summaries: list[RsiTimeframeSummary] = []
        touches: list[RsiTouch] = []
        for timeframe in self.timeframes:
            candles = _resample_closes(one_minute_closes, timeframe)
            rsi_values = wilder_rsi([close for _, close in candles], self.length)
            lower_touches = upper_touches = 0
            lower_zone_candles = upper_zone_candles = checked = 0
            previous_rsi: float | None = None

            for (timestamp, close), rsi in zip(candles, rsi_values):
                if rsi is None:
                    continue
                in_period = start <= timestamp.date() <= end
                if in_period:
                    checked += 1
                    if rsi <= self.lower:
                        lower_zone_candles += 1
                    if rsi >= self.upper:
                        upper_zone_candles += 1
                    if rsi <= self.lower and (previous_rsi is None or previous_rsi > self.lower):
                        lower_touches += 1
                        touches.append(RsiTouch(timestamp, timeframe, "LOWER", round(rsi, 2), close))
                    elif rsi >= self.upper and (previous_rsi is None or previous_rsi < self.upper):
                        upper_touches += 1
                        touches.append(RsiTouch(timestamp, timeframe, "UPPER", round(rsi, 2), close))
                previous_rsi = rsi

            summaries.append(RsiTimeframeSummary(
                timeframe, lower_touches, upper_touches,
                lower_zone_candles, upper_zone_candles, checked,
            ))

        return RsiTouchResult(
            start, end, self.length, self.lower, self.upper,
            tuple(summaries), tuple(sorted(touches, key=lambda item: item.timestamp)),
        )


class RsiLevelBacktester:
    """Five-minute option replay: 5% premium stop, opposite RSI level as target."""

    def __init__(
        self, length: int = 14, lower: float = 24, upper: float = 72,
        stop_pct: float = 0.05,
    ) -> None:
        RsiTouchAnalyzer(length, lower, upper, (5,))
        self.length = length
        self.lower = float(lower)
        self.upper = float(upper)
        self.stop_pct = stop_pct

    @staticmethod
    def _leg(snapshot: OptionSnapshot, side: str, strike: float):
        return next((leg for leg in snapshot.legs if leg.side == side and leg.strike == strike), None)

    @staticmethod
    def _atm(snapshot: OptionSnapshot) -> float:
        strikes = {leg.strike for leg in snapshot.legs}
        if not strikes:
            raise ValueError("Historical snapshot contains no option strikes")
        return min(strikes, key=lambda strike: abs(strike - snapshot.spot))

    def run(self, snapshots: Iterable[OptionSnapshot]) -> BacktestResult:
        ordered = sorted(snapshots, key=lambda item: item.timestamp)
        if not ordered:
            raise ValueError("No historical option snapshots were available")
        values = wilder_rsi([item.spot for item in ordered], self.length)
        trades: list[BacktestTrade] = []
        equity: list[float] = []
        open_trade: dict[str, object] | None = None
        pending: dict[str, object] | None = None
        previous: OptionSnapshot | None = None
        previous_rsi: float | None = None
        no_trade = 0

        def close(snapshot: OptionSnapshot, price: float, reason: str) -> None:
            nonlocal open_trade
            assert open_trade is not None
            pnl = round(price - float(open_trade["entry_price"]), 2)
            trades.append(BacktestTrade(
                opened_at=open_trade["opened_at"], closed_at=snapshot.timestamp,
                action=str(open_trade["action"]), strike=float(open_trade["strike"]),
                expiry=str(open_trade["expiry"]), entry_price=float(open_trade["entry_price"]),
                exit_price=round(price, 2), stop_price=float(open_trade["stop_price"]),
                target_price=0.0, confidence=float(open_trade["entry_rsi"]),
                setup=str(open_trade["setup"]), exit_reason=reason, pnl_points=pnl,
            ))
            equity.append(round((equity[-1] if equity else 0.0) + pnl, 2))
            open_trade = None

        for snapshot, rsi in zip(ordered, values):
            day = snapshot.timestamp.date()
            if open_trade:
                side = str(open_trade["side"])
                leg = self._leg(snapshot, side, float(open_trade["strike"]))
                if previous and previous.timestamp.date() != day:
                    old_leg = self._leg(previous, side, float(open_trade["strike"]))
                    close(previous, old_leg.ltp if old_leg else float(open_trade["entry_price"]), "End of day")
                elif leg:
                    stop = float(open_trade["stop_price"])
                    if (leg.low_price or leg.ltp) <= stop:
                        close(snapshot, stop, "5% stop loss")
                    elif rsi is not None:
                        reached_target = (
                            (side == "CE" and rsi >= self.upper)
                            or (side == "PE" and rsi <= self.lower)
                        )
                        if reached_target:
                            close(snapshot, leg.ltp, f"RSI target reached ({rsi:.2f})")

            if pending and not open_trade:
                if pending["day"] == day:
                    side = str(pending["side"])
                    leg = self._leg(snapshot, side, float(pending["strike"]))
                    if leg and (leg.open_price or leg.ltp) > 0:
                        entry = leg.open_price or leg.ltp
                        action = "BUY CE" if side == "CE" else "BUY PE"
                        open_trade = {
                            "opened_at": snapshot.timestamp, "action": action, "side": side,
                            "strike": leg.strike, "expiry": snapshot.expiry,
                            "entry_price": entry, "stop_price": round(entry * (1 - self.stop_pct), 2),
                            "entry_rsi": float(pending["rsi"]),
                            "setup": (
                                f"RSI({self.length}) {float(pending['rsi']):.2f}; "
                                f"exit at opposite RSI level or 5% stop"
                            ),
                        }
                pending = None

            if rsi is not None and not open_trade and not pending:
                if rsi <= self.lower and (previous_rsi is None or previous_rsi > self.lower):
                    pending = {"day": day, "side": "CE", "strike": self._atm(snapshot), "rsi": rsi}
                elif rsi >= self.upper and (previous_rsi is None or previous_rsi < self.upper):
                    pending = {"day": day, "side": "PE", "strike": self._atm(snapshot), "rsi": rsi}
                else:
                    no_trade += 1
            else:
                no_trade += 1
            previous = snapshot
            if rsi is not None:
                previous_rsi = rsi

        if open_trade and previous:
            side = str(open_trade["side"])
            leg = self._leg(previous, side, float(open_trade["strike"]))
            close(previous, leg.ltp if leg else float(open_trade["entry_price"]), "End of test")

        return BacktestResult(
            start_date=ordered[0].timestamp.date(), end_date=ordered[-1].timestamp.date(),
            snapshots=len(ordered), decisions=len(ordered), no_trade_decisions=no_trade,
            trades=tuple(trades), equity_curve=tuple(equity),
        )
