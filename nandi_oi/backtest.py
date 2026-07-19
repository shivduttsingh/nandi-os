from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable

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

    def __init__(self, stop_pct: float = 0.20, target_pct: float = 0.30, max_trades_daily: int = 3) -> None:
        if not 0 < stop_pct < 1 or target_pct <= 0:
            raise ValueError("Invalid stop or target percentage")
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.max_trades_daily = max_trades_daily

    @staticmethod
    def _leg(snapshot: OptionSnapshot, side: str, strike: float):
        return next((leg for leg in snapshot.legs if leg.side == side and leg.strike == strike), None)

    def run(self, snapshots: Iterable[OptionSnapshot]) -> BacktestResult:
        ordered = sorted(snapshots, key=lambda item: item.timestamp)
        if not ordered:
            raise ValueError("No historical snapshots were available for this period")

        engine = NandiOIEngine()
        engine_day: date | None = None
        open_trade: dict[str, object] | None = None
        trades: list[BacktestTrade] = []
        equity: list[float] = []
        trades_by_day: dict[date, int] = {}
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
                target_price=float(open_trade["target_price"]), confidence=float(open_trade["confidence"]),
                exit_reason=reason, pnl_points=pnl,
            ))
            equity.append(round((equity[-1] if equity else 0.0) + pnl, 2))
            open_trade = None

        previous: OptionSnapshot | None = None
        for snapshot in ordered:
            if open_trade:
                side = "CE" if open_trade["action"] == "BUY CE" else "PE"
                leg = self._leg(snapshot, side, float(open_trade["strike"]))
                if leg:
                    if leg.ltp <= float(open_trade["stop_price"]):
                        close(snapshot, leg.ltp, "Stop loss")
                    elif leg.ltp >= float(open_trade["target_price"]):
                        close(snapshot, leg.ltp, "Target")
                    elif previous and previous.timestamp.date() != snapshot.timestamp.date():
                        previous_leg = self._leg(previous, side, float(open_trade["strike"]))
                        close(previous, previous_leg.ltp if previous_leg else leg.ltp, "End of day")

            if snapshot.timestamp.date() != engine_day:
                engine = NandiOIEngine()
                engine_day = snapshot.timestamp.date()
            decision = engine.add_snapshot(snapshot)
            if decision.action == "NO TRADE":
                no_trade += 1
            elif not open_trade and trades_by_day.get(snapshot.timestamp.date(), 0) < self.max_trades_daily:
                side = "CE" if decision.action == "BUY CE" else "PE"
                leg = self._leg(snapshot, side, float(decision.selected_strike or 0))
                if leg and leg.ltp > 0:
                    entry = leg.ltp
                    open_trade = {
                        "opened_at": snapshot.timestamp, "action": decision.action,
                        "strike": leg.strike, "expiry": snapshot.expiry, "entry_price": entry,
                        "stop_price": round(entry * (1 - self.stop_pct), 2),
                        "target_price": round(entry * (1 + self.target_pct), 2),
                        "confidence": decision.confidence,
                    }
                    day = snapshot.timestamp.date()
                    trades_by_day[day] = trades_by_day.get(day, 0) + 1
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
