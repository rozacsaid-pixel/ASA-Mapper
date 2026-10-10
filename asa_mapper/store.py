"""SQLite persistence: library snapshot, review sessions, past mappings."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .models import PastMapping

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY CHECK (id = 1), xlsx BLOB NOT NULL, source TEXT NOT NULL, synced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, form_name TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
    review_json TEXT NOT NULL, decisions_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS past_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, form TEXT NOT NULL, data_json TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_library(self, xlsx: bytes, source: str) -> str:
        synced_at = now()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO library (id, xlsx, source, synced_at) VALUES (1, ?, ?, ?)",
                         (xlsx, source, synced_at))
        return synced_at

    def load_library(self) -> Optional[Tuple[bytes, str, str]]:
        with self._connect() as conn:
            row = conn.execute("SELECT xlsx, source, synced_at FROM library WHERE id = 1").fetchone()
        return (bytes(row["xlsx"]), row["source"], row["synced_at"]) if row else None

    def create_session(self, form_name: str, review: dict, decisions: Optional[dict] = None) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, form_name, created_at, review_json, decisions_json) VALUES (?, ?, ?, ?, ?)",
                (sid, form_name, now(), json.dumps(review), json.dumps(decisions or {})))
        return sid

    def get_session(self, sid: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "form_name": row["form_name"], "created_at": row["created_at"],
                "finished_at": row["finished_at"], "review": json.loads(row["review_json"]),
                "decisions": json.loads(row["decisions_json"])}

    def list_sessions(self) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, form_name, created_at, finished_at FROM sessions "
                                "ORDER BY created_at DESC, rowid DESC").fetchall()
        return [dict(r) for r in rows]

    def save_decisions(self, sid: str, decisions: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET decisions_json = ? WHERE id = ?", (json.dumps(decisions), sid))

    def finish_session(self, sid: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET finished_at = ? WHERE id = ?", (now(), sid))

    def replace_past(self, form: str, mappings: List[PastMapping]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM past_mappings WHERE form = ?", (form,))
            conn.executemany("INSERT INTO past_mappings (form, data_json) VALUES (?, ?)",
                             [(form, json.dumps(asdict(m))) for m in mappings])

    def list_past(self) -> List[PastMapping]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data_json FROM past_mappings ORDER BY id").fetchall()
        return [PastMapping(**json.loads(r["data_json"])) for r in rows]
