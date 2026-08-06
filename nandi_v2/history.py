from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Decision


class DecisionHistory:
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
            """)

    @staticmethod
    def signal_key(decision: Decision, spot: float, expiry: str) -> str:
        """One entry-alert key per side/strike/expiry/trading day.

        Spot and minute are deliberately excluded. A confirmed setup can remain
        above 80 for many refreshes without producing repeated email alerts.
        """
        stamp = decision.data_timestamp or decision.generated_at or datetime.utcnow()
        trading_day = stamp.strftime("%Y%m%d")
        strike = int(decision.selected_strike or 0)
        side = decision.side
        return f"{trading_day}:{side}:{strike}:{expiry}"

    def append(self, decision: Decision, spot: float, expiry: str, signal_key: str | None = None) -> str:
        key = signal_key or self.signal_key(decision, spot, expiry)
        payload = json.dumps(decision.to_record(), separators=(",", ":"))
        generated = decision.generated_at or datetime.utcnow()
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO decisions (signal_key, generated_at, data_timestamp, action, score, ce_score, pe_score, spot, expiry, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (key, generated.isoformat(), decision.data_timestamp.isoformat() if decision.data_timestamp else None, decision.action.value, decision.score, decision.ce_score, decision.pe_score, spot, expiry, payload))
        return key

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT generated_at, action, score, ce_score, pe_score, spot, expiry FROM decisions ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [{"Time": row["generated_at"], "Decision": row["action"], "Score": row["score"], "CE": row["ce_score"], "PE": row["pe_score"], "Spot": row["spot"], "Expiry": row["expiry"]} for row in rows]

    def alert_exists(self, signal_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM alerts WHERE signal_key = ? AND delivered = 1", (signal_key,)).fetchone()
        return row is not None

    def record_alert(self, signal_key: str, delivered: bool, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO alerts(signal_key, sent_at, delivered, error) VALUES (?, ?, ?, ?)", (signal_key, datetime.utcnow().isoformat(), int(delivered), error))

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM decisions")
            connection.execute("DELETE FROM alerts")
