import sqlite3
from datetime import datetime
from typing import List, Dict
from config.settings import JOURNAL_DB


class PaperJournal:
    def __init__(self, db_path=JOURNAL_DB):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT,
                    interval TEXT,
                    research_view TEXT,
                    scenario_studied TEXT,
                    confidence_score INTEGER,
                    risk_zone TEXT,
                    spot_price REAL,
                    invalidation_level TEXT,
                    research_zone TEXT,
                    notes TEXT
                )
                """
            )
            conn.commit()

    def save_event(self, result: Dict, notes: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO research_events(
                    created_at, symbol, interval, research_view, scenario_studied,
                    confidence_score, risk_zone, spot_price, invalidation_level,
                    research_zone, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    result.get("symbol"),
                    result.get("interval"),
                    result.get("research_view"),
                    result.get("scenario_studied"),
                    int(result.get("confidence_score", 0)),
                    result.get("risk_zone"),
                    float(result.get("spot_price", 0) or 0),
                    str(result.get("invalidation_level", "-")),
                    str(result.get("research_zone", "-")),
                    notes,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM research_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
