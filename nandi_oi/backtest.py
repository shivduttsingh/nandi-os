from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Callable, Iterable

from .engine import NandiOIEngine
from .models import OptionSnapshot


@dataclass(frozen=True)
class BacktestTrade:
    opened_at: datetime
    closed_at: datetime
    action: str
    strike: float
    expiry: str
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    confidence: float
    setup: str
    exit_reason: str
    pnl_points: float


@dataclass(frozen=True)
class BacktestResult:
    start_date: date
    end_date: date
    snapshots: int
    decisions: int
    no_trade_decisions: int
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[float, ...]

    @property
    def wins(self) -> int:
        return sum(trade.pnl_points > 0 for trade in self.trades)

    @property
    def losses(self) -> int:
        return sum(trade.pnl_points <= 0 for trade in self.trades)

    @property
    def win_rate(self) -> float:
        return self.wins / len(self.trades) * 100 if self.trades else 0.0

    @property
    def net_points(self) -> float:
        return round(sum(trade.pnl_points for trade in self.trades), 2)

    @property
    def max_drawdown(self) -> float:
        peak = drawdown = 0.0
        for value in (0.0, *self.equity_curve):
            peak = max(peak, value)
            drawdown = max(drawdown, peak - value)
        return round(drawdown, 2)

    def rows(self) -> list[dict[str, object]]:
        return [asdict(trade) for trade in self.trades]


class NandiBacktester:
    """Chronological, no-lookahead replay of the live Nandi decision engine."""

    def __init__(
        self, stop_pct: float = 0.20, target_pct: float = 0.30,
        max_trades_daily: int = 3, max_losses_daily: int | None = None,
        reset_snapshots: int = 0, engine_factory: Callable[[], NandiOIEngine] = NandiOIEngine,
    ) -> None:
        if not 0 < stop_pct < 1 or target_pct <= 0:
            raise ValueError("Invalid stop or target percentage")
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_daily = max_trades_daily
        self.max_losses_daily = max_losses_daily
        self.reset_snapshots = reset_snapshots
        self.engine_factory = engine_factory

    @staticmethod
    def _leg(snapshot: OptionSnapshot, side: str, strike: float):
        return next((leg for leg in snapshot.legs if leg.side == side and leg.strike == strike), None)

    def run(self, snapshots: Iterable[OptionSnapshot]) -> BacktestResult:
        ordered = sorted(snapshots, key=lambda item: item.timestamp)
        if not ordered:
            raise ValueError("No historical snapshots were available for this period")

        engine = self.engine_factory()
        engine_day: date | None = None
        open_trade: dict[str, object] | None = None
        trades: list[BacktestTrade] = []
        equity: list[float] = []
        trades_by_day: dict[date, int] = {}
        losses_by_day: dict[date, int] = {}
        no_trade = 0
        pending_signal: dict[str, object] | None = None
        locked_action: str | None = None
        no_trade_streak = 0

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
                setup=str(open_trade["setup"]),
                exit_reason=reason, pnl_points=pnl,
            ))
            equity.append(round((equity[-1] if equity else 0.0) + pnl, 2))
            if pnl <= 0:
                day = snapshot.timestamp.date()
                losses_by_day[day] = losses_by_day.get(day, 0) + 1
            open_trade = None

        previous: OptionSnapshot | None = None
        for snapshot in ordered:
            if open_trade:
                side = "CE" if open_trade["action"] == "BUY CE" else "PE"
                leg = self._leg(snapshot, side, float(open_trade["strike"]))
                if previous and previous.timestamp.date() != snapshot.timestamp.date():
                    previous_leg = self._leg(previous, side, float(open_trade["strike"]))
                    close(previous, previous_leg.ltp if previous_leg else float(open_trade["entry_price"]), "End of day")
                elif leg:
                    stop = float(open_trade["stop_price"])
                    target = float(open_trade["target_price"])
                    candle_low = leg.low_price or leg.ltp
                    candle_high = leg.high_price or leg.ltp
                    # Conservative ordering when both levels occur inside one five-minute candle.
                    if candle_low <= stop:
                        close(snapshot, stop, "Stop loss")
                    elif candle_high >= target:
                        close(snapshot, target, "Target")

            day = snapshot.timestamp.date()
            if pending_signal and not open_trade:
                signal_day = pending_signal["signaled_at"].date()
                losses_blocked = self.max_losses_daily is not None and losses_by_day.get(day, 0) >= self.max_losses_daily
                if signal_day == day and trades_by_day.get(day, 0) < self.max_trades_daily and not losses_blocked:
                    side = "CE" if pending_signal["action"] == "BUY CE" else "PE"
                    leg = self._leg(snapshot, side, float(pending_signal["strike"]))
                    if leg and (leg.open_price or leg.ltp) > 0:
                        entry = leg.open_price or leg.ltp
                        open_trade = {
                            "opened_at": snapshot.timestamp, "action": pending_signal["action"],
                            "strike": leg.strike, "expiry": snapshot.expiry, "entry_price": entry,
                            "stop_price": round(entry * (1 - self.stop_pct), 2),
                            "target_price": round(entry * (1 + self.target_pct), 2),
                            "confidence": pending_signal["confidence"], "setup": pending_signal["setup"],
                        }
                        trades_by_day[day] = trades_by_day.get(day, 0) + 1
                        locked_action = str(pending_signal["action"])
                pending_signal = None

            if snapshot.timestamp.date() != engine_day:
                engine = self.engine_factory()
                engine_day = snapshot.timestamp.date()
                pending_signal = None
                locked_action = None
                no_trade_streak = 0
            decision = engine.add_snapshot(snapshot)
            if decision.action == "NO TRADE":
                no_trade += 1
                no_trade_streak += 1
                if self.reset_snapshots and no_trade_streak >= self.reset_snapshots:
                    locked_action = None
            else:
                no_trade_streak = 0
                losses_blocked = self.max_losses_daily is not None and losses_by_day.get(day, 0) >= self.max_losses_daily
                if (
                    not open_trade and not pending_signal and decision.action != locked_action
                    and trades_by_day.get(day, 0) < self.max_trades_daily and not losses_blocked
                ):
                    pending_signal = {
                        "signaled_at": snapshot.timestamp, "action": decision.action,
                        "strike": float(decision.selected_strike or 0), "confidence": decision.confidence,
                        "setup": " | ".join(decision.reasons),
                    }
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
