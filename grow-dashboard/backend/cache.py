"""Small SQLite-backed cache with TTL semantics.

Used for two things:
- The radar/Jira background refresh result (single row each, TTL enforced by the
  scheduler's own interval, not by readers).
- Data Quality results, cached per (analytical_name, days) combo, TTL-checked on
  read so a "Run Checks" click can decide cached-vs-live itself.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def set(self, key: str, payload: Any) -> str:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache (key, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(payload), updated_at),
            )
        return updated_at

    def get(self, key: str) -> tuple[Any, str] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, updated_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        payload_json, updated_at = row
        return json.loads(payload_json), updated_at

    def is_fresh(self, updated_at: str, ttl_hours: float) -> bool:
        updated = datetime.fromisoformat(updated_at)
        age = datetime.now(timezone.utc) - updated
        return age.total_seconds() < ttl_hours * 3600
