from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from shiv_v1.engine import SimilarityStats
from .replay import CalibrationSample


@dataclass(frozen=True)
class V2TradeRecord:
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
    pattern: str
    hold_minutes: float


class ShivV2ResearchStore:
    """Experimental local V2 ledger; never treated as broker-grade persistence."""

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
                CREATE TABLE IF NOT EXISTS v2_trades (
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
                    pattern TEXT NOT NULL,
                    hold_minutes REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_shiv_v2_signature ON v2_trades(signature, interval_minutes, side)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_shiv_v2_regime ON v2_trades(regime, interval_minutes, side)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v2_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    snapshot_key TEXT NOT NULL UNIQUE,
                    signature TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setup_quality REAL NOT NULL,
                    required_quality REAL NOT NULL,
                    mtf_agreement REAL NOT NULL,
                    required_mtf REAL NOT NULL,
                    strike REAL,
                    strike_offset INTEGER,
                    strike_score REAL NOT NULL,
                    persistence INTEGER NOT NULL,
                    session_bucket TEXT NOT NULL,
                    volatility_band TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    entry_status TEXT NOT NULL
                )
                """
            )

    def record_observation(
        self,
        *,
        observed_at: datetime,
        snapshot_key: str,
        signature: str,
        interval_minutes: int,
        side: str,
        regime: str,
        status: str,
        setup_quality: float,
        required_quality: float,
        mtf_agreement: float,
        required_mtf: float,
        strike: float | None,
        strike_offset: int | None,
        strike_score: float,
        persistence: int,
        session_bucket: str,
        volatility_band: str,
        pattern: str,
        entry_status: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO v2_observations (
                    observed_at, snapshot_key, signature, interval_minutes, side, regime,
                    status, setup_quality, required_quality, mtf_agreement, required_mtf,
                    strike, strike_offset, strike_score, persistence, session_bucket,
                    volatility_band, pattern, entry_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at.isoformat(), snapshot_key, signature, int(interval_minutes), side,
                    regime, status, float(setup_quality), float(required_quality), float(mtf_agreement),
                    float(required_mtf), float(strike) if strike is not None else None,
                    int(strike_offset) if strike_offset is not None else None, float(strike_score),
                    int(persistence), session_bucket, volatility_band, pattern, entry_status,
                ),
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
        pattern: str,
    ) -> None:
        points = float(exit_price) - float(entry_price)
        hold_minutes = max(0.0, (closed_at - opened_at).total_seconds() / 60.0)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_trades (
                    opened_at, closed_at, signature, interval_minutes, side, strike, strike_offset,
                    entry_price, exit_price, points, exit_reason, setup_quality, mtf_agreement,
                    strike_score, persistence, regime, session_bucket, volatility_band, pattern,
                    hold_minutes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opened_at.isoformat(), closed_at.isoformat(), signature, int(interval_minutes),
                    side, float(strike), int(strike_offset), float(entry_price), float(exit_price),
                    points, exit_reason, float(setup_quality), float(mtf_agreement), float(strike_score),
                    int(persistence), regime, session_bucket, volatility_band, pattern, hold_minutes,
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
                FROM v2_trades
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

    def recent_trades(self, limit: int = 200) -> tuple[V2TradeRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT opened_at, closed_at, signature, interval_minutes, side, strike, strike_offset,
                       entry_price, exit_price, points, exit_reason, setup_quality, mtf_agreement,
                       strike_score, persistence, regime, session_bucket, volatility_band, pattern,
                       hold_minutes
                FROM v2_trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return tuple(V2TradeRecord(
            opened_at=str(row["opened_at"]), closed_at=str(row["closed_at"]),
            signature=str(row["signature"]), interval_minutes=int(row["interval_minutes"]),
            side=str(row["side"]), strike=float(row["strike"]), strike_offset=int(row["strike_offset"]),
            entry_price=float(row["entry_price"]), exit_price=float(row["exit_price"]),
            points=float(row["points"]), exit_reason=str(row["exit_reason"]),
            setup_quality=float(row["setup_quality"]), mtf_agreement=float(row["mtf_agreement"]),
            strike_score=float(row["strike_score"]), persistence=int(row["persistence"]),
            regime=str(row["regime"]), session_bucket=str(row["session_bucket"]),
            volatility_band=str(row["volatility_band"]), pattern=str(row["pattern"]),
            hold_minutes=float(row["hold_minutes"]),
        ) for row in rows)

    def calibration_samples(self, limit: int = 1000) -> tuple[CalibrationSample, ...]:
        records = tuple(reversed(self.recent_trades(limit)))
        samples: list[CalibrationSample] = []
        for row in records:
            try:
                timestamp = datetime.fromisoformat(row.opened_at)
            except ValueError:
                continue
            samples.append(CalibrationSample(
                timestamp=timestamp,
                regime=row.regime,
                interval_minutes=row.interval_minutes,
                side=row.side,
                setup_quality=row.setup_quality,
                mtf_agreement=row.mtf_agreement,
                strike_score=row.strike_score,
                persistence=row.persistence,
                points=row.points,
                session_bucket=row.session_bucket,
                volatility_band=row.volatility_band,
                pattern=row.pattern,
            ))
        return tuple(samples)

    def observation_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM v2_observations").fetchone()
        return int(row["count"] or 0)
