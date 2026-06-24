import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
from config.settings import MEMORY_DB


class MemoryStore:
    """Simple local SQLite memory for Nandi OS V2.

    Memory is intentionally local-first. It stores user-approved notes, preferences,
    research lessons, and system observations. This is not vector search yet; it is
    the stable base layer before adding embeddings/RAG.
    """

    def __init__(self, db_path=MEMORY_DB):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 3,
                    source TEXT DEFAULT 'manual',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(self, content: str, category: str = "general", importance: int = 3, source: str = "manual") -> int:
        content = (content or "").strip()
        if not content:
            raise ValueError("Memory content cannot be empty.")
        created_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories(category, content, importance, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (category, content, importance, source, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        q = f"%{(query or '').strip()}%"
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE content LIKE ? OR category LIKE ? OR source LIKE ?
                ORDER BY importance DESC, id DESC
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent(self, limit: int = 20) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def count(self, category: Optional[str] = None) -> int:
        with self._connect() as conn:
            if category:
                return int(conn.execute("SELECT COUNT(*) FROM memories WHERE category=?", (category,)).fetchone()[0])
            return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
