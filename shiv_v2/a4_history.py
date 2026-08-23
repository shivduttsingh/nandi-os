from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from shiv_v1.engine import SimilarityStats
from .replay import CalibrationSample


@dataclass(frozen=True)
class A4TradeRecord:
    opened_at: str
    closed_at: str
    signature: str
    interval_minutes: int
    side: str
    strike: float
    strike_offset: int
    entry_price: float
    exit_price: float
    points: float
    exit_reason: str
    setup_quality: float
    mtf_agreement: float
    strike_score: float
    persistence: int
    regime: str
    session_bucket: str
    volatility_band: str
    hold_minutes: float


class A4ResearchStore:
    """Local A++++ paper-research ledger. It is not broker-grade persistence."""

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
                CREATE TABLE IF NOT EXISTS a4_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    strike REAL NOT NULL,
                    strike_offset INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    points REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    setup_quality REAL NOT NULL,
                    mtf_agreement REAL NOT NULL,
                    strike_score REAL NOT NULL,
                    persistence INTEGER NOT NULL,
                    regime TEXT NOT NULL,
                    session_bucket TEXT NOT NULL,
                    volatility_band TEXT NOT NULL,
                    hold_minutes REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_a4_signature ON a4_trades(signature, interval_minutes, side)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_a4_regime ON a4_trades(regime, interval_minutes, side)"
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
        strike_offset: int,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        setup_quality: float,
        mtf_agreement: float,
        strike_score: float,
        persistence: int,
        regime: str,
        session_bucket: str,
        volatility_band: str,
    ) -> None:
        points = float(exit_price) - float(entry_price)
        hold_minutes = max(0.0, (closed_at - opened_at).total_seconds() / 60.0)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO a4_trades (
                    opened_at, closed_at, signature, interval_minutes, side, strike, strike_offset,
                    entry_price, exit_price, points, exit_reason, setup_quality, mtf_agreement,
                    strike_score, persistence, regime, session_bucket, volatility_band, hold_minutes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opened_at.isoformat(), closed_at.isoformat(), signature, int(interval_minutes),
                    side, float(strike), int(strike_offset), float(entry_price), float(exit_price),
                    points, exit_reason, float(setup_quality), float(mtf_agreement), float(strike_score),
                    int(persistence), regime, session_bucket, volatility_band, hold_minutes,
                ),
            )

    def similarity_stats(
        self,
        signature: str,
        interval_minutes: int,
        side: str,
        *,
        validation_sample: int = 50,
    ) -> SimilarityStats:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS sample_size,
                       SUM(CASE WHEN points > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN points <= 0 THEN 1 ELSE 0 END) AS losses,
                       AVG(points) AS average_points
                FROM a4_trades
                WHERE signature = ? AND interval_minutes = ? AND side = ?
                """,
                (signature, int(interval_minutes), side),
            ).fetchone()
        sample = int(row["sample_size"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        win_rate = wins / sample * 100.0 if sample else None
        average = float(row["average_points"]) if row["average_points"] is not None else None
        status = f"VALIDATED SAMPLE ({sample})" if sample >= validation_sample else f"UNVALIDATED ({sample}/{validation_sample})"
        return SimilarityStats(
            sample_size=sample,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 1) if win_rate is not None else None,
            average_points=round(average, 2) if average is not None else None,
            status=status,
        )

    def recent_trades(self, limit: int = 300) -> tuple[A4TradeRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT opened_at, closed_at, signature, interval_minutes, side, strike, strike_offset,
                       entry_price, exit_price, points, exit_reason, setup_quality, mtf_agreement,
                       strike_score, persistence, regime, session_bucket, volatility_band, hold_minutes
                FROM a4_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return tuple(
            A4TradeRecord(
                opened_at=str(row["opened_at"]),
                closed_at=str(row["closed_at"]),
                signature=str(row["signature"]),
                interval_minutes=int(row["interval_minutes"]),
                side=str(row["side"]),
                strike=float(row["strike"]),
                strike_offset=int(row["strike_offset"]),
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                points=float(row["points"]),
                exit_reason=str(row["exit_reason"]),
                setup_quality=float(row["setup_quality"]),
                mtf_agreement=float(row["mtf_agreement"]),
                strike_score=float(row["strike_score"]),
                persistence=int(row["persistence"]),
                regime=str(row["regime"]),
                session_bucket=str(row["session_bucket"]),
                volatility_band=str(row["volatility_band"]),
                hold_minutes=float(row["hold_minutes"]),
            )
            for row in rows
        )

    def calibration_samples(self, limit: int = 1000) -> tuple[CalibrationSample, ...]:
        records = tuple(reversed(self.recent_trades(limit)))
        output: list[CalibrationSample] = []
        for record in records:
            try:
                timestamp = datetime.fromisoformat(record.closed_at)
            except ValueError:
                continue
            output.append(
                CalibrationSample(
                    timestamp=timestamp,
                    regime=record.regime,
                    interval_minutes=record.interval_minutes,
                    side=record.side,
                    setup_quality=record.setup_quality,
                    mtf_agreement=record.mtf_agreement,
                    strike_score=record.strike_score,
                    persistence=record.persistence,
                    points=record.points,
                    session_bucket=record.session_bucket,
                    volatility_band=record.volatility_band,
                    pattern="EXCLUDED",
                )
            )
        return tuple(output)
