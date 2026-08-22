from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .engine import SimilarityStats


@dataclass(frozen=True)
class PaperTradeRecord:
    opened_at: str
    closed_at: str
    signature: str
    interval_minutes: int
    side: str
    strike: float
    entry_price: float
    exit_price: float
    points: float
    exit_reason: str
    setup_quality: float


class ShivResearchStore:
    """Small local research ledger for the experimental Shiv deployment.

    Streamlit Community Cloud local storage can reset when an app is rebuilt, so
    this is a research convenience rather than a permanent broker-grade ledger.
    """

    def __init__(self, path: str = "data/shiv_research.sqlite") -> None:
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    strike REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    points REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    setup_quality REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_shiv_signature ON paper_trades(signature, interval_minutes, side)"
            )

    def record_trade(
        self,
        *,
        opened_at: datetime,
        closed_at: datetime,
        signature: str,
        interval_minutes: int,
        side: str,
        strike: float,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        setup_quality: float,
    ) -> None:
        points = float(exit_price) - float(entry_price)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_trades (
                    opened_at, closed_at, signature, interval_minutes, side, strike,
                    entry_price, exit_price, points, exit_reason, setup_quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opened_at.isoformat(),
                    closed_at.isoformat(),
                    signature,
                    int(interval_minutes),
                    side,
                    float(strike),
                    float(entry_price),
                    float(exit_price),
                    points,
                    exit_reason,
                    float(setup_quality),
                ),
            )

    def similarity_stats(
        self,
        signature: str,
        interval_minutes: int,
        side: str,
        *,
        validation_sample: int = 20,
    ) -> SimilarityStats:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS sample_size,
                       SUM(CASE WHEN points > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN points <= 0 THEN 1 ELSE 0 END) AS losses,
                       AVG(points) AS average_points
                FROM paper_trades
                WHERE signature = ? AND interval_minutes = ? AND side = ?
                """,
                (signature, int(interval_minutes), side),
            ).fetchone()
        sample = int(row["sample_size"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        win_rate = wins / sample * 100.0 if sample else None
        average = float(row["average_points"]) if row["average_points"] is not None else None
        status = (
            f"VALIDATED SAMPLE ({sample})"
            if sample >= validation_sample
            else f"UNVALIDATED ({sample}/{validation_sample})"
        )
        return SimilarityStats(
            sample_size=sample,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 1) if win_rate is not None else None,
            average_points=round(average, 2) if average is not None else None,
            status=status,
        )

    def recent_trades(self, limit: int = 100) -> tuple[PaperTradeRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT opened_at, closed_at, signature, interval_minutes, side, strike,
                       entry_price, exit_price, points, exit_reason, setup_quality
                FROM paper_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return tuple(
            PaperTradeRecord(
                opened_at=str(row["opened_at"]),
                closed_at=str(row["closed_at"]),
                signature=str(row["signature"]),
                interval_minutes=int(row["interval_minutes"]),
                side=str(row["side"]),
                strike=float(row["strike"]),
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                points=float(row["points"]),
                exit_reason=str(row["exit_reason"]),
                setup_quality=float(row["setup_quality"]),
            )
            for row in rows
        )
