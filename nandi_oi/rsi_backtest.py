from __future__ import annotations

from datetime import date
from typing import Iterable

from .backtest import BacktestResult, BacktestTrade
from .models import OptionSnapshot


def wilder_rsi(values: list[float], length: int = 14) -> list[float | None]:
    """Wilder RSI with no future-data access."""
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


class RSI2472Backtester:
    """RSI(14) contrarian option-buyer replay: ≤24 CE, ≥72 PE."""

    def __init__(
        self, stop_pct: float = 0.20, target_pct: float = 0.30,
        max_trades_daily: int = 3, length: int = 14,
    ) -> None:
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_daily = max_trades_daily
        self.length = length

    @staticmethod
    def _leg(snapshot: OptionSnapshot, side: str, strike: float):
        return next((leg for leg in snapshot.legs if leg.side == side and leg.strike == strike), None)

    @staticmethod
    def _atm_strike(snapshot: OptionSnapshot) -> float:
        strikes = {leg.strike for leg in snapshot.legs}
        if not strikes:
            raise ValueError("Historical snapshot contains no option strikes")
        return min(strikes, key=lambda strike: abs(strike - snapshot.spot))

    def run(self, snapshots: Iterable[OptionSnapshot]) -> BacktestResult:
        ordered = sorted(snapshots, key=lambda item: item.timestamp)
        if not ordered:
            raise ValueError("No historical snapshots were available for this period")
        rsi_values = wilder_rsi([item.spot for item in ordered], self.length)
        trades: list[BacktestTrade] = []
        equity: list[float] = []
        trades_by_day: dict[date, int] = {}
        open_trade: dict[str, object] | None = None
        pending: dict[str, object] | None = None
        locked_side: str | None = None
        no_trade = 0
        previous: OptionSnapshot | None = None

        def close(snapshot: OptionSnapshot, price: float, reason: str) -> None:
            nonlocal open_trade
            assert open_trade is not None
            pnl = round(price - float(open_trade["entry_price"]), 2)
            trades.append(BacktestTrade(
                opened_at=open_trade["opened_at"], closed_at=snapshot.timestamp,
                action=str(open_trade["action"]), strike=float(open_trade["strike"]),
                expiry=str(open_trade["expiry"]), entry_price=float(open_trade["entry_price"]),
                exit_price=round(price, 2), stop_price=float(open_trade["stop_price"]),
                target_price=float(open_trade["target_price"]), confidence=float(open_trade["confidence"]),
                setup=str(open_trade["setup"]), exit_reason=reason, pnl_points=pnl,
            ))
            equity.append(round((equity[-1] if equity else 0.0) + pnl, 2))
            open_trade = None

        for index, snapshot in enumerate(ordered):
            day = snapshot.timestamp.date()
            if open_trade:
                side = "CE" if open_trade["action"] == "BUY CE" else "PE"
                leg = self._leg(snapshot, side, float(open_trade["strike"]))
                if previous and previous.timestamp.date() != day:
                    previous_leg = self._leg(previous, side, float(open_trade["strike"]))
                    close(previous, previous_leg.ltp if previous_leg else float(open_trade["entry_price"]), "End of day")
                elif leg:
                    stop = float(open_trade["stop_price"])
                    target = float(open_trade["target_price"])
                    if (leg.low_price or leg.ltp) <= stop:
                        close(snapshot, stop, "Stop loss")
                    elif (leg.high_price or leg.ltp) >= target:
                        close(snapshot, target, "Target")

            if pending and not open_trade:
                if pending["signaled_at"].date() == day and trades_by_day.get(day, 0) < self.max_trades_daily:
                    side = str(pending["side"])
                    leg = self._leg(snapshot, side, float(pending["strike"]))
                    if leg and (leg.open_price or leg.ltp) > 0:
                        entry = leg.open_price or leg.ltp
                        action = "BUY CE" if side == "CE" else "BUY PE"
                        open_trade = {
                            "opened_at": snapshot.timestamp, "action": action, "strike": leg.strike,
                            "expiry": snapshot.expiry, "entry_price": entry,
                            "stop_price": round(entry * (1 - self.stop_pct), 2),
                            "target_price": round(entry * (1 + self.target_pct), 2),
                            "confidence": float(pending["rsi"]),
                            "setup": f"RSI(14) {float(pending['rsi']):.2f} triggered {action}",
                        }
                        trades_by_day[day] = trades_by_day.get(day, 0) + 1
                        locked_side = side
                pending = None

            rsi = rsi_values[index]
            signal_side: str | None = None
            if rsi is not None:
                if locked_side == "CE" and rsi > 35:
                    locked_side = None
                elif locked_side == "PE" and rsi < 65:
                    locked_side = None
                if rsi <= 24 and locked_side != "CE":
                    signal_side = "CE"
                elif rsi >= 72 and locked_side != "PE":
                    signal_side = "PE"

            if signal_side and not open_trade and not pending and trades_by_day.get(day, 0) < self.max_trades_daily:
                pending = {
                    "signaled_at": snapshot.timestamp, "side": signal_side,
                    "strike": self._atm_strike(snapshot), "rsi": rsi,
                }
            else:
                no_trade += 1
            previous = snapshot

        if open_trade and previous:
            side = "CE" if open_trade["action"] == "BUY CE" else "PE"
            leg = self._leg(previous, side, float(open_trade["strike"]))
            close(previous, leg.ltp if leg else float(open_trade["entry_price"]), "End of test")

        return BacktestResult(
            start_date=ordered[0].timestamp.date(), end_date=ordered[-1].timestamp.date(),
            snapshots=len(ordered), decisions=len(ordered), no_trade_decisions=no_trade,
            trades=tuple(trades), equity_curve=tuple(equity),
        )
