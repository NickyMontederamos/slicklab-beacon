"""Web inbox users. Admins see every client; client users see only their own profile."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from pathlib import Path

from .config import validate_client_id
from .store import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'client')),
    client_id TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Users:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, name: str, role: str, client_id: str | None = None) -> str:
        if role == "client":
            validate_client_id(client_id or "")
        elif role != "admin":
            raise ValueError("role must be 'admin' or 'client'")
        token = secrets.token_urlsafe(24)
        with self._conn() as c:
            c.execute(
                "INSERT INTO users (name, role, client_id, token_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, role, client_id if role == "client" else None, _hash(token), now()),
            )
        return token

    def authenticate(self, token: str) -> dict | None:
        if not token:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE token_hash = ? AND disabled = 0", (_hash(token.strip()),)
            ).fetchone()
        return dict(row) if row else None

    def get(self, user_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id").fetchall()]

    def disable(self, user_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))


def can_access(user: dict, client_id: str) -> bool:
    return user["role"] == "admin" or user.get("client_id") == client_id
