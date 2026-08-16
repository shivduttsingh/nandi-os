from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .fundamentals import FundamentalBias, FundamentalFactor
from .lifecycle import TradeState, TradeStatus
from .models import Decision, MarketContext, OptionChainSnapshot, OptionLeg, StrikeRow


class DecisionHistory:
    """Persistent Nandi V2 decision, alert, lifecycle and replay journal."""

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

                CREATE TABLE IF NOT EXISTS market_frames (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    frame_key TEXT NOT NULL UNIQUE,
                    observed_at TEXT NOT NULL,
                    data_timestamp TEXT NOT NULL,
                    expiry TEXT,
                    spot REAL NOT NULL,
                    snapshot_payload TEXT NOT NULL,
                    context_payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_frames_observed_at ON market_frames(observed_at ASC);

                CREATE TABLE IF NOT EXISTS fundamental_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    factor_key TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    bias TEXT NOT NULL,
                    impact REAL NOT NULL,
                    confidence REAL NOT NULL,
                    max_age_minutes INTEGER NOT NULL,
                    source TEXT,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_fundamental_factor_id
                    ON fundamental_events(factor_key, id DESC);
            """)

    @staticmethod
    def signal_key(decision: Decision, spot: float, expiry: str) -> str:
        stamp = decision.data_timestamp or decision.generated_at or datetime.now(timezone.utc)
        trading_day = stamp.strftime("%Y%m%d")
        strike = int(decision.selected_strike or 0)
        return f"{trading_day}:{decision.side}:{strike}:{expiry}"

    def append(self, decision: Decision, spot: float, expiry: str, signal_key: str | None = None) -> str:
        key = signal_key or self.signal_key(decision, spot, expiry)
        payload = json.dumps(decision.to_record(), separators=(",", ":"))
        generated = decision.generated_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO decisions
                (signal_key, generated_at, data_timestamp, action, score, ce_score, pe_score, spot, expiry, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            {"Time": r["generated_at"], "Decision": r["action"], "Score": r["score"], "CE": r["ce_score"], "PE": r["pe_score"], "Spot": r["spot"], "Expiry": r["expiry"]}
            for r in rows
        ]

    def alert_exists(self, signal_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM alerts WHERE signal_key = ? AND delivered = 1", (signal_key,)).fetchone()
        return row is not None

    def record_alert(self, signal_key: str, delivered: bool, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO alerts(signal_key, sent_at, delivered, error) VALUES (?, ?, ?, ?)",
                (signal_key, datetime.now(timezone.utc).isoformat(), int(delivered), error),
            )

    @staticmethod
    def _dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

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
            opened_at=cls._dt(payload.get("opened_at")),
            updated_at=cls._dt(payload.get("updated_at")),
            partial_booked=bool(payload.get("partial_booked", False)),
            peak_favourable_spot=payload.get("peak_favourable_spot"),
            reason=str(payload.get("reason") or ""),
        )

    @staticmethod
    def trade_event_key(state: TradeState, decision: Decision | None = None) -> str:
        stamp = state.updated_at or state.opened_at or datetime.now(timezone.utc)
        decision_stamp = decision.data_timestamp.isoformat() if decision and decision.data_timestamp else ""
        return ":".join([stamp.isoformat(), state.status.value, state.side, str(int(state.selected_strike or 0)), decision_stamp])

    def append_trade_event(self, state: TradeState, *, spot: float | None = None, decision: Decision | None = None) -> bool:
        key = self.trade_event_key(state, decision)
        payload = json.dumps(self._trade_payload(state), separators=(",", ":"))
        event_at = state.updated_at or state.opened_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO trade_events
                (event_key, event_at, status, side, spot, score, selected_strike, reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key, event_at.isoformat(), state.status.value, state.side, spot, decision.score if decision else None, state.selected_strike, state.reason, payload),
            )
        return cursor.rowcount > 0

    def latest_trade_state(self) -> TradeState:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM trade_events ORDER BY id DESC LIMIT 1").fetchone()
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
                "SELECT event_at, status, side, spot, score, selected_strike, reason FROM trade_events ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {"Time": r["event_at"], "Status": r["status"], "Side": r["side"], "Spot": r["spot"], "Score": r["score"], "Strike": r["selected_strike"], "Reason": r["reason"]}
            for r in rows
        ]

    def trade_events(
        self, start_date: date | None = None, end_date: date | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Return lifecycle events chronologically for auditable result summaries."""
        query = "SELECT event_at, status, side, spot, score, selected_strike, reason FROM trade_events"
        clauses: list[str] = []
        args: list[Any] = []
        if start_date is not None:
            clauses.append("event_at >= ?")
            args.append(start_date.isoformat())
        if end_date is not None:
            clauses.append("event_at < ?")
            args.append((end_date + timedelta(days=1)).isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY event_at ASC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(args)).fetchall()
        return [
            {"Time": r["event_at"], "Status": r["status"], "Side": r["side"], "Spot": r["spot"], "Score": r["score"], "Strike": r["selected_strike"], "Reason": r["reason"]}
            for r in rows
        ]

    @staticmethod
    def _leg_payload(leg: OptionLeg) -> dict[str, float]:
        return {"ltp": leg.ltp, "change": leg.change, "oi": leg.oi, "change_oi": leg.change_oi, "volume": leg.volume, "iv": leg.iv}

    @classmethod
    def _snapshot_payload(cls, snapshot: OptionChainSnapshot) -> dict[str, Any]:
        return {
            "timestamp": snapshot.timestamp.isoformat(),
            "expiry": snapshot.expiry,
            "spot": snapshot.spot,
            "source": snapshot.source,
            "raw_timestamp": snapshot.raw_timestamp,
            "rows": [{"strike": row.strike, "ce": cls._leg_payload(row.ce), "pe": cls._leg_payload(row.pe)} for row in snapshot.rows],
        }

    @staticmethod
    def _context_payload(context: MarketContext) -> dict[str, Any]:
        return {
            "observed_at": context.observed_at.isoformat(),
            "previous_spot": context.previous_spot,
            "recent_high": context.recent_high,
            "recent_low": context.recent_low,
            "momentum_rsi": context.momentum_rsi,
            "spot_volume_ratio": context.spot_volume_ratio,
        }

    @staticmethod
    def _leg_from_payload(value: dict[str, Any]) -> OptionLeg:
        return OptionLeg(
            ltp=float(value.get("ltp") or 0.0), change=float(value.get("change") or 0.0),
            oi=float(value.get("oi") or 0.0), change_oi=float(value.get("change_oi") or 0.0),
            volume=float(value.get("volume") or 0.0), iv=float(value.get("iv") or 0.0),
        )

    @classmethod
    def _snapshot_from_payload(cls, value: dict[str, Any]) -> OptionChainSnapshot:
        rows = tuple(
            StrikeRow(float(row.get("strike") or 0.0), cls._leg_from_payload(row.get("ce") or {}), cls._leg_from_payload(row.get("pe") or {}))
            for row in value.get("rows", []) if isinstance(row, dict)
        )
        return OptionChainSnapshot(
            timestamp=datetime.fromisoformat(str(value["timestamp"])), expiry=str(value.get("expiry") or ""),
            spot=float(value.get("spot") or 0.0), rows=rows, source=str(value.get("source") or "NSE"), raw_timestamp=str(value.get("raw_timestamp") or ""),
        )

    @classmethod
    def _context_from_payload(cls, value: dict[str, Any]) -> MarketContext:
        return MarketContext(
            observed_at=datetime.fromisoformat(str(value["observed_at"])), previous_spot=value.get("previous_spot"),
            recent_high=value.get("recent_high"), recent_low=value.get("recent_low"), momentum_rsi=value.get("momentum_rsi"),
            spot_volume_ratio=value.get("spot_volume_ratio"),
        )

    def append_market_frame(self, snapshot: OptionChainSnapshot, context: MarketContext) -> bool:
        key = f"{snapshot.timestamp.isoformat()}:{snapshot.expiry}"
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO market_frames
                (frame_key, observed_at, data_timestamp, expiry, spot, snapshot_payload, context_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, context.observed_at.isoformat(), snapshot.timestamp.isoformat(), snapshot.expiry, snapshot.spot,
                    json.dumps(self._snapshot_payload(snapshot), separators=(",", ":")),
                    json.dumps(self._context_payload(context), separators=(",", ":")),
                ),
            )
        return cursor.rowcount > 0

    def replay_data(self, trading_day: str | None = None, limit: int = 2000) -> tuple[list[OptionChainSnapshot], list[MarketContext]]:
        query = "SELECT snapshot_payload, context_payload FROM market_frames"
        args: list[Any] = []
        if trading_day:
            query += " WHERE substr(observed_at, 1, 10) = ?"
            args.append(trading_day)
        query += " ORDER BY observed_at ASC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(args)).fetchall()
        snapshots: list[OptionChainSnapshot] = []
        contexts: list[MarketContext] = []
        for row in rows:
            try:
                sp = json.loads(row["snapshot_payload"])
                cp = json.loads(row["context_payload"])
                snapshots.append(self._snapshot_from_payload(sp))
                contexts.append(self._context_from_payload(cp))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return snapshots, contexts

    def replay_days(self, limit: int = 60) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT substr(observed_at,1,10) AS day FROM market_frames ORDER BY day DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["day"]) for row in rows if row["day"]]

    def append_fundamental_factors(
        self,
        factors: list[FundamentalFactor] | tuple[FundamentalFactor, ...],
        *,
        recorded_at: datetime | None = None,
    ) -> int:
        stamp = recorded_at or datetime.now(timezone.utc)
        rows = [
            (
                factor.key,
                stamp.isoformat(),
                factor.observed_at.isoformat(),
                factor.name,
                factor.category,
                factor.bias.value,
                max(0.0, min(100.0, float(factor.impact))),
                max(0.0, min(1.0, float(factor.confidence))),
                max(1, int(factor.max_age_minutes)),
                factor.source,
                factor.note,
            )
            for factor in factors
        ]
        if not rows:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO fundamental_events
                (factor_key, recorded_at, observed_at, name, category, bias, impact,
                 confidence, max_age_minutes, source, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def latest_fundamental_factors(self) -> tuple[FundamentalFactor, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT event.factor_key, event.observed_at, event.name, event.category,
                          event.bias, event.impact, event.confidence,
                          event.max_age_minutes, event.source, event.note
                   FROM fundamental_events AS event
                   INNER JOIN (
                       SELECT factor_key, MAX(id) AS latest_id
                       FROM fundamental_events
                       GROUP BY factor_key
                   ) AS latest ON latest.latest_id = event.id
                   ORDER BY event.factor_key"""
            ).fetchall()
        factors: list[FundamentalFactor] = []
        for row in rows:
            try:
                bias = FundamentalBias(str(row["bias"]))
                observed_at = datetime.fromisoformat(str(row["observed_at"]))
            except (TypeError, ValueError):
                continue
            factors.append(
                FundamentalFactor(
                    key=str(row["factor_key"]),
                    name=str(row["name"]),
                    category=str(row["category"]),
                    bias=bias,
                    impact=float(row["impact"]),
                    confidence=float(row["confidence"]),
                    observed_at=observed_at,
                    max_age_minutes=int(row["max_age_minutes"]),
                    source=str(row["source"] or "Manual research input"),
                    note=str(row["note"] or ""),
                )
            )
        return tuple(factors)

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM decisions")
            connection.execute("DELETE FROM alerts")
            connection.execute("DELETE FROM trade_events")
            connection.execute("DELETE FROM market_frames")
            connection.execute("DELETE FROM fundamental_events")
