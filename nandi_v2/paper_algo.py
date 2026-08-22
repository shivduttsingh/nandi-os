from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from nandi_oi.models import OptionStrikeCandles


ATM_ALGO = "ATM Strategy Algo"
ATM_TWO_STRIKE_ALGO = "ATM ±2 Strategy Algo"
PAPER_QUANTITY = 130
PAPER_PROFIT_POINTS = 8.0
PAPER_STOP_POINTS = 4.0
PAPER_MAX_HOLD_MINUTES = 45.0
PAPER_COOLDOWN_MINUTES = 5.0
PAPER_MAX_DAILY_TRADES = 3


@dataclass(frozen=True)
class PaperPosition:
    strategy: str
    side: str
    strike: float
    expiry: str
    quantity: int
    entry_premium: float
    current_premium: float
    target_premium: float
    stop_premium: float
    opened_at: datetime
    updated_at: datetime
    signal_key: str

    @property
    def premium_points(self) -> float:
        return self.current_premium - self.entry_premium

    @property
    def paper_pnl(self) -> float:
        return self.premium_points * self.quantity


@dataclass(frozen=True)
class PaperTrade:
    strategy: str
    side: str
    strike: float
    expiry: str
    quantity: int
    entry_premium: float
    exit_premium: float
    target_premium: float
    stop_premium: float
    opened_at: datetime
    closed_at: datetime
    premium_points: float
    paper_pnl: float
    exit_reason: str
    signal_key: str


@dataclass(frozen=True)
class PaperAlgoUpdate:
    strategy: str
    position: PaperPosition | None
    closed_trade: PaperTrade | None
    message: str


def directional_two_strike_contract(
    strike_series: Iterable[OptionStrikeCandles],
    side: str,
) -> OptionStrikeCandles | None:
    """Use the two-strike OTM contract for the ATM±2 paper book."""
    offset = 2 if side == "CE" else -2 if side == "PE" else None
    if offset is None:
        return None
    return next((item for item in strike_series if item.offset == offset), None)


class PaperAlgoStore:
    """SQLite journal for two isolated, broker-free paper algorithms."""

    def __init__(self, path: str = "data/nandi_v2.sqlite") -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_algo_state (
                    strategy TEXT PRIMARY KEY,
                    last_signal_key TEXT NOT NULL DEFAULT '',
                    last_exit_at TEXT,
                    position_payload TEXT
                );

                CREATE TABLE IF NOT EXISTS paper_algo_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry TEXT,
                    quantity INTEGER NOT NULL,
                    entry_premium REAL NOT NULL,
                    exit_premium REAL NOT NULL,
                    target_premium REAL NOT NULL,
                    stop_premium REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    premium_points REAL NOT NULL,
                    paper_pnl REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    signal_key TEXT NOT NULL,
                    UNIQUE(strategy, signal_key)
                );
                CREATE INDEX IF NOT EXISTS idx_paper_algo_trades_closed_at
                    ON paper_algo_trades(closed_at DESC);
                """
            )

    @staticmethod
    def _position_payload(position: PaperPosition) -> str:
        payload = asdict(position)
        payload["opened_at"] = position.opened_at.isoformat()
        payload["updated_at"] = position.updated_at.isoformat()
        return json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _position_from_payload(payload: str | None) -> PaperPosition | None:
        if not payload:
            return None
        try:
            value = json.loads(payload)
            return PaperPosition(
                strategy=str(value["strategy"]),
                side=str(value["side"]),
                strike=float(value["strike"]),
                expiry=str(value.get("expiry") or ""),
                quantity=int(value["quantity"]),
                entry_premium=float(value["entry_premium"]),
                current_premium=float(value["current_premium"]),
                target_premium=float(value["target_premium"]),
                stop_premium=float(value["stop_premium"]),
                opened_at=datetime.fromisoformat(str(value["opened_at"])),
                updated_at=datetime.fromisoformat(str(value["updated_at"])),
                signal_key=str(value["signal_key"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def position(self, strategy: str) -> PaperPosition | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT position_payload FROM paper_algo_state WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        return self._position_from_payload(row["position_payload"] if row else None)

    def last_signal_key(self, strategy: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_signal_key FROM paper_algo_state WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        return str(row["last_signal_key"] or "") if row else ""

    def last_exit_at(self, strategy: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_exit_at FROM paper_algo_state WHERE strategy = ?",
                (strategy,),
            ).fetchone()
        if row is None or not row["last_exit_at"]:
            return None
        try:
            return datetime.fromisoformat(str(row["last_exit_at"]))
        except ValueError:
            return None

    def save_position(self, position: PaperPosition) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO paper_algo_state
                (strategy, last_signal_key, last_exit_at, position_payload)
                VALUES (?, ?, NULL, ?)
                ON CONFLICT(strategy) DO UPDATE SET
                    last_signal_key = excluded.last_signal_key,
                    position_payload = excluded.position_payload""",
                (
                    position.strategy,
                    position.signal_key,
                    self._position_payload(position),
                ),
            )

    def close_position(self, trade: PaperTrade) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO paper_algo_trades
                (strategy, side, strike, expiry, quantity, entry_premium, exit_premium,
                 target_premium, stop_premium, opened_at, closed_at, premium_points,
                 paper_pnl, exit_reason, signal_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.strategy,
                    trade.side,
                    trade.strike,
                    trade.expiry,
                    trade.quantity,
                    trade.entry_premium,
                    trade.exit_premium,
                    trade.target_premium,
                    trade.stop_premium,
                    trade.opened_at.isoformat(),
                    trade.closed_at.isoformat(),
                    trade.premium_points,
                    trade.paper_pnl,
                    trade.exit_reason,
                    trade.signal_key,
                ),
            )
            connection.execute(
                """INSERT INTO paper_algo_state
                (strategy, last_signal_key, last_exit_at, position_payload)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(strategy) DO UPDATE SET
                    last_signal_key = excluded.last_signal_key,
                    last_exit_at = excluded.last_exit_at,
                    position_payload = NULL""",
                (trade.strategy, trade.signal_key, trade.closed_at.isoformat()),
            )

    def completed_on(self, strategy: str, trading_day: date) -> int:
        start = trading_day.isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS total FROM paper_algo_trades
                WHERE strategy = ? AND substr(closed_at, 1, 10) = ?""",
                (strategy, start),
            ).fetchone()
        return int(row["total"] or 0) if row else 0

    def recent_trades(
        self,
        strategy: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = """SELECT strategy, side, strike, expiry, quantity, entry_premium,
            exit_premium, opened_at, closed_at, premium_points, paper_pnl, exit_reason
            FROM paper_algo_trades"""
        args: list[Any] = []
        if strategy:
            query += " WHERE strategy = ?"
            args.append(strategy)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(args)).fetchall()
        return [
            {
                "Algo": row["strategy"],
                "Side": row["side"],
                "Strike": row["strike"],
                "Expiry": row["expiry"],
                "Qty": row["quantity"],
                "Entry": row["entry_premium"],
                "Exit": row["exit_premium"],
                "Points": row["premium_points"],
                "Paper P&L": row["paper_pnl"],
                "Opened": row["opened_at"],
                "Closed": row["closed_at"],
                "Exit reason": row["exit_reason"],
            }
            for row in rows
        ]


def _elapsed_minutes(start: datetime, end: datetime) -> float:
    if start.tzinfo is None and end.tzinfo is not None:
        start = start.replace(tzinfo=end.tzinfo)
    elif start.tzinfo is not None and end.tzinfo is None:
        end = end.replace(tzinfo=start.tzinfo)
    return max(0.0, (end - start).total_seconds() / 60.0)


def advance_paper_algo(
    store: PaperAlgoStore,
    *,
    strategy: str,
    signal_side: str,
    signal_key: str,
    strike: float,
    expiry: str,
    premium: float,
    now: datetime,
    market_open: bool,
    quantity: int = PAPER_QUANTITY,
    profit_points: float = PAPER_PROFIT_POINTS,
    stop_points: float = PAPER_STOP_POINTS,
    maximum_hold_minutes: float = PAPER_MAX_HOLD_MINUTES,
    cooldown_minutes: float = PAPER_COOLDOWN_MINUTES,
    maximum_daily_trades: int = PAPER_MAX_DAILY_TRADES,
) -> PaperAlgoUpdate:
    """Advance one independent paper book using observed premium prices only."""
    if quantity <= 0:
        raise ValueError("Paper quantity must be positive")
    if profit_points <= 0 or stop_points <= 0:
        raise ValueError("Paper profit and stop points must be positive")
    if maximum_daily_trades < 1:
        raise ValueError("Maximum daily paper trades must be positive")

    position = store.position(strategy)
    if position is not None:
        if premium <= 0:
            if _elapsed_minutes(position.opened_at, now) >= maximum_hold_minutes:
                premium = position.current_premium
            else:
                return PaperAlgoUpdate(strategy, position, None, "Waiting for a valid live premium.")
        updated = PaperPosition(
            **{
                **asdict(position),
                "current_premium": float(premium),
                "updated_at": now,
            }
        )
        exit_premium: float | None = None
        exit_reason = ""
        if premium <= position.stop_premium:
            exit_premium = position.stop_premium
            distance = position.entry_premium - position.stop_premium
            exit_reason = f"Paper stop booked at −{distance:g} premium points."
        elif premium >= position.target_premium:
            exit_premium = position.target_premium
            distance = position.target_premium - position.entry_premium
            exit_reason = f"Small paper profit booked at +{distance:g} premium points."
        elif _elapsed_minutes(position.opened_at, now) >= maximum_hold_minutes:
            exit_premium = float(premium)
            exit_reason = f"Maximum {maximum_hold_minutes:g}-minute paper hold reached at the last observed premium."

        if exit_premium is None:
            store.save_position(updated)
            return PaperAlgoUpdate(strategy, updated, None, "Paper position is open.")

        points = round(exit_premium - position.entry_premium, 2)
        trade = PaperTrade(
            strategy=strategy,
            side=position.side,
            strike=position.strike,
            expiry=position.expiry,
            quantity=position.quantity,
            entry_premium=position.entry_premium,
            exit_premium=round(exit_premium, 2),
            target_premium=position.target_premium,
            stop_premium=position.stop_premium,
            opened_at=position.opened_at,
            closed_at=now,
            premium_points=points,
            paper_pnl=round(points * position.quantity, 2),
            exit_reason=exit_reason,
            signal_key=position.signal_key,
        )
        store.close_position(trade)
        return PaperAlgoUpdate(strategy, None, trade, exit_reason)

    if not market_open:
        return PaperAlgoUpdate(strategy, None, None, "NSE session is closed; no paper entry.")
    if signal_side not in {"CE", "PE"}:
        return PaperAlgoUpdate(strategy, None, None, "Waiting for a confirmed CE or PE signal.")
    if premium <= 0 or strike <= 0 or not signal_key:
        return PaperAlgoUpdate(strategy, None, None, "Confirmed side is missing a valid paper contract premium.")
    if signal_key == store.last_signal_key(strategy):
        return PaperAlgoUpdate(strategy, None, None, "This completed-candle signal was already used.")
    if store.completed_on(strategy, now.date()) >= maximum_daily_trades:
        return PaperAlgoUpdate(
            strategy,
            None,
            None,
            f"Daily limit of {maximum_daily_trades} completed paper trades reached.",
        )
    last_exit = store.last_exit_at(strategy)
    if last_exit is not None and _elapsed_minutes(last_exit, now) < cooldown_minutes:
        left = cooldown_minutes - _elapsed_minutes(last_exit, now)
        return PaperAlgoUpdate(
            strategy,
            None,
            None,
            f"Paper cooldown active for {left:.1f} more minute(s).",
        )

    entry = round(float(premium), 2)
    position = PaperPosition(
        strategy=strategy,
        side=signal_side,
        strike=float(strike),
        expiry=expiry,
        quantity=int(quantity),
        entry_premium=entry,
        current_premium=entry,
        target_premium=round(entry + profit_points, 2),
        stop_premium=round(max(0.05, entry - stop_points), 2),
        opened_at=now,
        updated_at=now,
        signal_key=signal_key,
    )
    store.save_position(position)
    return PaperAlgoUpdate(strategy, position, None, "Confirmed signal opened a paper position.")
