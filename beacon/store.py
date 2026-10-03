"""Per-client SQLite storage. Each client gets its own database file, so data never mixes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- faq | explainer
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    fact_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | published
    critic TEXT,                     -- JSON critic report
    critic_verdict TEXT,             -- clean | flagged | unchecked
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES drafts(id),
    action TEXT NOT NULL,            -- approve | reject | edit | publish | reopen
    actor TEXT NOT NULL,
    note TEXT,
    content_hash TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_runs (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    passed INTEGER NOT NULL,
    warned INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    results TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visibility_runs (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    runs_per_question INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | done | failed
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS visibility_answers (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES visibility_runs(id),
    question TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    answer TEXT NOT NULL,
    mentioned INTEGER NOT NULL,
    matched TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n\0\n{body}".encode()).hexdigest()


class Store:
    def __init__(self, client_dir: Path):
        client_dir.mkdir(parents=True, exist_ok=True)
        self.path = client_dir / "beacon.db"
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- events ------------------------------------------------------------------

    def log(self, actor: str, kind: str, detail: Any = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (at, actor, kind, detail) VALUES (?, ?, ?, ?)",
                (now(), actor, kind, json.dumps(detail) if detail is not None else None),
            )

    def events(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- drafts ------------------------------------------------------------------

    def add_draft(self, kind: str, title: str, body: str, fact_ids: list[str], actor: str) -> int:
        ts = now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO drafts (kind, title, body, fact_ids, status, critic_verdict, "
                "created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', 'unchecked', ?, ?, ?)",
                (kind, title, body, json.dumps(fact_ids), actor, ts, ts),
            )
            return int(cur.lastrowid)

    def get_draft(self, draft_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return _draft(row) if row else None

    def list_drafts(self, status: str | None = None) -> list[dict]:
        with self._conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM drafts WHERE status = ? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = c.execute("SELECT * FROM drafts ORDER BY id DESC").fetchall()
        return [_draft(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM drafts GROUP BY status").fetchall()
        out = {"pending": 0, "approved": 0, "rejected": 0, "published": 0}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    def set_critic(self, draft_id: int, report: dict, verdict: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE drafts SET critic = ?, critic_verdict = ?, updated_at = ? WHERE id = ?",
                (json.dumps(report), verdict, now(), draft_id),
            )

    def update_body(self, draft_id: int, title: str, body: str, actor: str, note: str = "") -> None:
        """Edits always send the draft back to 'pending' - an approval covers exact content only."""
        with self._conn() as c:
            c.execute(
                "UPDATE drafts SET title = ?, body = ?, status = 'pending', "
                "critic_verdict = 'unchecked', critic = NULL, updated_at = ? WHERE id = ?",
                (title, body, now(), draft_id),
            )
            c.execute(
                "INSERT INTO decisions (draft_id, action, actor, note, content_hash, at) "
                "VALUES (?, 'edit', ?, ?, ?, ?)",
                (draft_id, actor, note, content_hash(title, body), now()),
            )

    def decide(self, draft_id: int, action: str, actor: str, note: str = "") -> None:
        new_status = {
            "approve": "approved",
            "reject": "rejected",
            "publish": "published",
            "reopen": "pending",
        }[action]
        d = self.get_draft(draft_id)
        if d is None:
            raise KeyError(draft_id)
        if d["status"] == "published":
            raise ValueError(f"Draft #{draft_id} is already published; its record is final.")
        with self._conn() as c:
            c.execute(
                "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now(), draft_id),
            )
            c.execute(
                "INSERT INTO decisions (draft_id, action, actor, note, content_hash, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (draft_id, action, actor, note, content_hash(d["title"], d["body"]), now()),
            )

    def decisions(self, draft_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM decisions WHERE draft_id = ? ORDER BY id", (draft_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_approval(self, draft_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM decisions WHERE draft_id = ? AND action = 'approve' "
                "ORDER BY id DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
        return dict(row) if row else None

    # -- audits ------------------------------------------------------------------

    def add_audit(self, results: list[dict]) -> int:
        n = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "warn", "fail")}
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO audit_runs (at, passed, warned, failed, results) VALUES (?,?,?,?,?)",
                (now(), n["pass"], n["warn"], n["fail"], json.dumps(results)),
            )
            return int(cur.lastrowid)

    def audits(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM audit_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(r), "results": json.loads(r["results"])} for r in rows]

    # -- visibility --------------------------------------------------------------

    def start_visibility_run(self, label: str, provider: str, model: str, runs: int) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO visibility_runs (label, provider, model, runs_per_question, "
                "started_at) VALUES (?, ?, ?, ?, ?)",
                (label, provider, model, runs, now()),
            )
            return int(cur.lastrowid)

    def add_answer(
        self,
        run_id: int,
        question: str,
        attempt: int,
        answer: str,
        mentioned: bool,
        matched: list[str],
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO visibility_answers (run_id, question, attempt, answer, mentioned, "
                "matched, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, question, attempt, answer, int(mentioned), json.dumps(matched), error),
            )

    def finish_visibility_run(self, run_id: int, status: str = "done") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE visibility_runs SET status = ?, finished_at = ? WHERE id = ?",
                (status, now(), run_id),
            )

    def visibility_runs(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT r.*, COUNT(a.id) AS answers, "
                "SUM(CASE WHEN a.error IS NULL THEN 1 ELSE 0 END) AS valid, "
                "SUM(CASE WHEN a.error IS NULL THEN a.mentioned ELSE 0 END) AS mentions "
                "FROM visibility_runs r LEFT JOIN visibility_answers a ON a.run_id = r.id "
                "GROUP BY r.id ORDER BY r.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def visibility_run(self, run_id: int) -> dict | None:
        runs = [r for r in self.visibility_runs() if r["id"] == run_id]
        return runs[0] if runs else None

    def answers(self, run_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM visibility_answers WHERE run_id = ? ORDER BY question, attempt",
                (run_id,),
            ).fetchall()
        return [{**dict(r), "matched": json.loads(r["matched"] or "[]")} for r in rows]


def _draft(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["fact_ids"] = json.loads(d["fact_ids"] or "[]")
    d["critic"] = json.loads(d["critic"]) if d["critic"] else None
    return d
