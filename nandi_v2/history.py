from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .lifecycle import TradeState, TradeStatus
from .models import Decision


class DecisionHistory:
    """Persistent Nandi V2 decision, alert and trade-lifecycle journal."""

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
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    data_timestamp TEXT,
                    action TEXT NOT NULL,
                    score REAL NOT NULL,
                    ce_score REAL NOT NULL,
                    pe_score REAL NOT NULL,
                    spot REAL NOT NULL,
                    expiry TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_generated_at ON decisions(generated_at DESC);

                CREATE TABLE IF NOT EXISTS alerts (
                    signal_key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL,
                    delivered INTEGER NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    event_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    side TEXT NOT NULL,
                    spot REAL,
                    score REAL,
                    selected_strike REAL,
                    reason TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_events_event_at ON trade_events(event_at DESC);
            """)

    @staticmethod
    def signal_key(decision: Decision, spot: float, expiry: str) -> str:
        """One successful entry-alert key per side/strike/expiry/trading day."""
        stamp = decision.data_timestamp or decision.generated_at or datetime.utcnow()
        trading_day = stamp.strftime("%Y%m%d")
        strike = int(decision.selected_strike or 0)
        return f"{trading_day}:{decision.side}:{strike}:{expiry}"

    def append(self, decision: Decision, spot: float, expiry: str, signal_key: str | None = None) -> str:
        key = signal_key or self.signal_key(decision, spot, expiry)
        payload = json.dumps(decision.to_record(), separators=(",", ":"))
        generated = decision.generated_at or datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions
                (signal_key, generated_at, data_timestamp, action, score, ce_score, pe_score, spot, expiry, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    generated.isoformat(),
                    decision.data_timestamp.isoformat() if decision.data_timestamp else None,
                    decision.action.value,
                    decision.score,
                    decision.ce_score,
                    decision.pe_score,
                    spot,
                    expiry,
                    payload,
                ),
            )
        return key

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT generated_at, action, score, ce_score, pe_score, spot, expiry FROM decisions ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "Time": row["generated_at"],
                "Decision": row["action"],
                "Score": row["score"],
                "CE": row["ce_score"],
                "PE": row["pe_score"],
                "Spot": row["spot"],
                "Expiry": row["expiry"],
            }
            for row in rows
        ]

    def alert_exists(self, signal_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM alerts WHERE signal_key = ? AND delivered = 1", (signal_key,)
            ).fetchone()
        return row is not None

    def record_alert(self, signal_key: str, delivered: bool, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO alerts(signal_key, sent_at, delivered, error) VALUES (?, ?, ?, ?)",
                (signal_key, datetime.utcnow().isoformat(), int(delivered), error),
            )

    @staticmethod
    def _trade_payload(state: TradeState) -> dict[str, Any]:
        return {
            "status": state.status.value,
            "side": state.side,
            "entry_spot": state.entry_spot,
            "stop_spot": state.stop_spot,
            "target_1": state.target_1,
            "target_2": state.target_2,
            "selected_strike": state.selected_strike,
            "opened_at": state.opened_at.isoformat() if state.opened_at else None,
            "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            "partial_booked": state.partial_booked,
            "peak_favourable_spot": state.peak_favourable_spot,
            "reason": state.reason,
        }

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @classmethod
    def _state_from_payload(cls, payload: dict[str, Any]) -> TradeState:
        try:
            status = TradeStatus(str(payload.get("status", TradeStatus.FLAT.value)))
        except ValueError:
            status = TradeStatus.FLAT
        return TradeState(
            status=status,
            side=str(payload.get("side") or "NONE"),
            entry_spot=payload.get("entry_spot"),
            stop_spot=payload.get("stop_spot"),
            target_1=payload.get("target_1"),
            target_2=payload.get("target_2"),
            selected_strike=payload.get("selected_strike"),
            opened_at=cls._parse_datetime(payload.get("opened_at")),
            updated_at=cls._parse_datetime(payload.get("updated_at")),
            partial_booked=bool(payload.get("partial_booked", False)),
            peak_favourable_spot=payload.get("peak_favourable_spot"),
            reason=str(payload.get("reason") or ""),
        )

    @staticmethod
    def trade_event_key(state: TradeState, decision: Decision | None = None) -> str:
        stamp = state.updated_at or state.opened_at or datetime.utcnow()
        decision_stamp = ""
        if decision is not None and decision.data_timestamp is not None:
            decision_stamp = decision.data_timestamp.isoformat()
        return ":".join(
            [
                stamp.isoformat(),
                state.status.value,
                state.side,
                str(int(state.selected_strike or 0)),
                decision_stamp,
            ]
        )

    def append_trade_event(
        self,
        state: TradeState,
        *,
        spot: float | None = None,
        decision: Decision | None = None,
    ) -> bool:
        """Persist a lifecycle transition once. Returns True if a new row was inserted."""
        key = self.trade_event_key(state, decision)
        payload = json.dumps(self._trade_payload(state), separators=(",", ":"))
        event_at = state.updated_at or state.opened_at or datetime.utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO trade_events
                (event_key, event_at, status, side, spot, score, selected_strike, reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    event_at.isoformat(),
                    state.status.value,
                    state.side,
                    spot,
                    decision.score if decision is not None else None,
                    state.selected_strike,
                    state.reason,
                    payload,
                ),
            )
        return cursor.rowcount > 0

    def latest_trade_state(self) -> TradeState:
        """Restore the last lifecycle state after an app restart."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM trade_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return TradeState()
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            return TradeState()
        return self._state_from_payload(payload if isinstance(payload, dict) else {})

    def recent_trade_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_at, status, side, spot, score, selected_strike, reason
                FROM trade_events ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "Time": row["event_at"],
                "Status": row["status"],
                "Side": row["side"],
                "Spot": row["spot"],
                "Score": row["score"],
                "Strike": row["selected_strike"],
                "Reason": row["reason"],
            }
            for row in rows
        ]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM decisions")
            connection.execute("DELETE FROM alerts")
            connection.execute("DELETE FROM trade_events")
