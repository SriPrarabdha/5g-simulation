from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wall_time TEXT NOT NULL,
                run_id TEXT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        self._connection.commit()

    def append(self, run_id: str | None, actor: str, action: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO audit_events(wall_time, run_id, actor, action, payload) VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), run_id, actor, action,
                 json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def list(self, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            if run_id is None:
                rows = self._connection.execute(
                    "SELECT id, wall_time, run_id, actor, action, payload FROM audit_events ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT id, wall_time, run_id, actor, action, payload FROM audit_events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                    (run_id, limit),
                ).fetchall()
        return [{"id": row[0], "wall_time": row[1], "run_id": row[2], "actor": row[3],
                 "action": row[4], "payload": json.loads(row[5])} for row in rows]

