from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .evidence import live_evidence
from .models import Decision, OptionSnapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    spot REAL NOT NULL,
    spot_change REAL NOT NULL,
    recent_high REAL NOT NULL,
    recent_low REAL NOT NULL,
    expiry TEXT NOT NULL,
    legs_json TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_captured_at ON snapshots(captured_at DESC);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    decided_at TEXT NOT NULL,
    action TEXT NOT NULL,
    bullish_score REAL NOT NULL,
    bearish_score REAL NOT NULL,
    setup_quality REAL NOT NULL,
    approved INTEGER NOT NULL,
    selected_strike REAL,
    reasons_json TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_decided_at ON decisions(decided_at DESC);

CREATE TABLE IF NOT EXISTS worker_health (
    worker_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    market_state TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    last_snapshot TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    decision_id INTEGER REFERENCES decisions(id),
    delivered INTEGER NOT NULL DEFAULT 0,
    delivery_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS alerts_created_at ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS daily_reports (
    trading_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    snapshots INTEGER NOT NULL,
    approved_setups INTEGER NOT NULL,
    wait_decisions INTEGER NOT NULL,
    buy_ce_setups INTEGER NOT NULL,
    buy_pe_setups INTEGER NOT NULL,
    maximum_bullish REAL NOT NULL,
    maximum_bearish REAL NOT NULL,
    maximum_quality REAL NOT NULL,
    summary TEXT NOT NULL
);
"""


class NandiStore:
    """Durable SQLite history shared by the local dashboard and local worker."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("NANDI_DB_PATH", "data/nandi.db")
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def save_analysis(self, snapshot: OptionSnapshot, decision: Decision, source: str = "worker") -> int:
        legs_json = json.dumps([asdict(leg) for leg in snapshot.legs], separators=(",", ":"))
        evidence_json = json.dumps(live_evidence(snapshot, decision), separators=(",", ":"))
        timestamp = snapshot.timestamp.isoformat(timespec="seconds")
        with self._connect() as connection:
            snapshot_id = int(connection.execute(
                """
                INSERT INTO snapshots(captured_at, spot, spot_change, recent_high, recent_low, expiry, legs_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, snapshot.spot, snapshot.spot_change, snapshot.recent_high, snapshot.recent_low,
                 snapshot.expiry, legs_json, source),
            ).lastrowid)
            return int(connection.execute(
                """
                INSERT INTO decisions(snapshot_id, decided_at, action, bullish_score, bearish_score, setup_quality,
                    approved, selected_strike, reasons_json, blockers_json, evidence_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, timestamp, decision.action, decision.bullish_score, decision.bearish_score,
                 decision.confidence, int(decision.approved), decision.selected_strike,
                 json.dumps(decision.reasons), json.dumps(decision.blockers), evidence_json, source),
            ).lastrowid)

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("reasons_json", "blockers_json", "evidence_json", "legs_json"):
            if key in result:
                result[key.removesuffix("_json")] = json.loads(result.pop(key))
        return result

    def latest_analysis(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, s.spot, s.spot_change, s.recent_high, s.recent_low, s.expiry, s.legs_json
                FROM decisions d JOIN snapshots s ON s.id = d.snapshot_id
                ORDER BY d.decided_at DESC LIMIT 1
                """
            ).fetchone()
        return self._decode(row)

    def recent_decisions(self, limit: int = 250, trading_day: date | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT d.id, d.decided_at, d.action, d.bullish_score, d.bearish_score,
                   d.setup_quality, d.selected_strike, d.source, s.spot, s.expiry
            FROM decisions d JOIN snapshots s ON s.id = d.snapshot_id
        """
        values: list[Any] = []
        if trading_day:
            query += " WHERE substr(d.decided_at, 1, 10) = ?"
            values.append(trading_day.isoformat())
        query += " ORDER BY d.decided_at DESC LIMIT ?"
        values.append(max(1, min(int(limit), 5000)))
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def heartbeat(self, status: str, market_state: str, *, worker_name: str = "nandi-live",
                  last_snapshot: datetime | None = None, error: str = "", now: datetime | None = None) -> None:
        heartbeat = (now or datetime.now()).isoformat(timespec="seconds")
        last_snapshot_value = last_snapshot.isoformat(timespec="seconds") if last_snapshot else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO worker_health(worker_name, status, market_state, last_heartbeat, last_snapshot, last_error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    status=excluded.status, market_state=excluded.market_state,
                    last_heartbeat=excluded.last_heartbeat,
                    last_snapshot=COALESCE(excluded.last_snapshot, worker_health.last_snapshot),
                    last_error=excluded.last_error
                """,
                (worker_name, status, market_state, heartbeat, last_snapshot_value, error[:1000]),
            )

    def worker_status(self, worker_name: str = "nandi-live") -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM worker_health WHERE worker_name = ?", (worker_name,)).fetchone()
        return dict(row) if row else None

    def record_alert(self, level: str, title: str, message: str, decision_id: int | None = None,
                     *, delivered: bool = False, delivery_error: str = "", now: datetime | None = None) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                """
                INSERT INTO alerts(created_at, level, title, message, decision_id, delivered, delivery_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ((now or datetime.now()).isoformat(timespec="seconds"), level, title, message,
                 decision_id, int(delivered), delivery_error[:1000]),
            ).lastrowid)

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_report(self, trading_day: date) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM daily_reports WHERE trading_date = ?", (trading_day.isoformat(),)).fetchone()
        return dict(row) if row else None

    def build_daily_report(self, trading_day: date, now: datetime | None = None) -> dict[str, Any] | None:
        if self.daily_report(trading_day):
            return self.daily_report(trading_day)
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) snapshots, SUM(CASE WHEN action != 'NO TRADE' THEN 1 ELSE 0 END) approved_setups,
                       SUM(CASE WHEN action = 'NO TRADE' THEN 1 ELSE 0 END) wait_decisions,
                       SUM(CASE WHEN action = 'BUY CE' THEN 1 ELSE 0 END) buy_ce_setups,
                       SUM(CASE WHEN action = 'BUY PE' THEN 1 ELSE 0 END) buy_pe_setups,
                       MAX(bullish_score) maximum_bullish, MAX(bearish_score) maximum_bearish,
                       MAX(setup_quality) maximum_quality
                FROM decisions WHERE substr(decided_at, 1, 10) = ?
                """, (trading_day.isoformat(),)
            ).fetchone()
            if not totals or int(totals["snapshots"] or 0) == 0:
                return None
            values = {key: (value or 0) for key, value in dict(totals).items()}
            summary = (
                f"{values['snapshots']} snapshots; {values['approved_setups']} approved setups "
                f"({values['buy_ce_setups']} CE, {values['buy_pe_setups']} PE); {values['wait_decisions']} WAIT decisions; "
                f"maximum setup quality {float(values['maximum_quality']):.1f}/100."
            )
            connection.execute(
                """
                INSERT INTO daily_reports(trading_date, generated_at, snapshots, approved_setups, wait_decisions,
                    buy_ce_setups, buy_pe_setups, maximum_bullish, maximum_bearish, maximum_quality, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trading_day.isoformat(), (now or datetime.now()).isoformat(timespec="seconds"), int(values["snapshots"]),
                 int(values["approved_setups"]), int(values["wait_decisions"]), int(values["buy_ce_setups"]),
                 int(values["buy_pe_setups"]), float(values["maximum_bullish"]), float(values["maximum_bearish"]),
                 float(values["maximum_quality"]), summary),
            )
        return self.daily_report(trading_day)

    def latest_daily_report(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM daily_reports ORDER BY trading_date DESC LIMIT 1").fetchone()
        return dict(row) if row else None
