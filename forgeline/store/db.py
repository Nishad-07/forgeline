"""SQLite persistence for runs, workflow events, LLM calls, approvals, audit log, ledger and cache.

LangGraph's own checkpoints live in a separate file (see Settings.checkpoint_path). This database is
the observability layer: the CLI and the API server both read and write it, so a run started from the
CLI appears live on the dashboard. WAL mode allows one writer and many readers across processes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    requirement_id TEXT,
    title TEXT,
    requirement_path TEXT,
    provider TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    node TEXT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id, id);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts TEXT NOT NULL,
    agent TEXT,
    provider TEXT,
    model TEXT,
    tier TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    cached INTEGER DEFAULT 0,
    attempt INTEGER DEFAULT 1,
    status TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_run ON llm_calls (run_id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    gate TEXT NOT NULL,
    decision TEXT NOT NULL,
    actor TEXT,
    comment TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
CREATE TABLE IF NOT EXISTS ledger (
    key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action TEXT NOT NULL,
    ts TEXT NOT NULL,
    inputs TEXT NOT NULL,
    result TEXT
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,
    ts TEXT NOT NULL,
    response TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0
);
"""


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ low level
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ runs
    def create_run(
        self,
        run_id: str,
        *,
        requirement_id: str = "",
        title: str = "",
        requirement_path: str = "",
        provider: str = "",
        status: str = "created",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        self.execute(
            "INSERT INTO runs (id, requirement_id, title, requirement_path, provider, status, created_at,"
            " updated_at, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, requirement_id, title, requirement_path, provider, status, now, now, dumps(data or {})),
        )
        return self.get_run(run_id)  # type: ignore[return-value]

    def update_run(
        self, run_id: str, *, status: str | None = None, provider: str | None = None, **data: Any
    ) -> None:
        """Update status/provider and merge keyword arguments into the run's JSON data."""
        with self.transaction() as conn:
            row = conn.execute("SELECT data FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            merged = {**json.loads(row["data"]), **data}
            conn.execute(
                "UPDATE runs SET status = COALESCE(?, status), provider = COALESCE(?, provider),"
                " data = ?, updated_at = ? WHERE id = ?",
                (status, provider, dumps(merged), utcnow_iso(), run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not rows:
            return None
        run = rows[0]
        run["data"] = json.loads(run["data"])
        return run

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        runs = self.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        for run in runs:
            run["data"] = json.loads(run["data"])
        return runs

    # ------------------------------------------------------------------ events
    def add_event(self, run_id: str, kind: str, node: str | None = None, **payload: Any) -> int:
        cur = self.execute(
            "INSERT INTO events (run_id, ts, node, kind, payload) VALUES (?, ?, ?, ?, ?)",
            (run_id, utcnow_iso(), node, kind, dumps(payload)),
        )
        return int(cur.lastrowid or 0)

    def events(self, run_id: str, after_id: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?", (run_id, after_id, limit)
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    # ------------------------------------------------------------------ llm calls
    def record_llm_call(self, **call: Any) -> int:
        cols = [
            "run_id", "agent", "provider", "model", "tier", "input_tokens", "output_tokens",
            "latency_ms", "cost_usd", "cached", "attempt", "status", "error",
        ]
        values = [call.get(c) for c in cols]
        cur = self.execute(
            f"INSERT INTO llm_calls (ts, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            (utcnow_iso(), *values),
        )
        return int(cur.lastrowid or 0)

    def llm_calls(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            return self.query("SELECT * FROM llm_calls ORDER BY id")
        return self.query("SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))

    def usage_summary(self, run_id: str) -> dict[str, Any]:
        rows = self.query(
            "SELECT agent, provider, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
            " SUM(output_tokens) AS output_tokens, SUM(latency_ms) AS latency_ms, SUM(cost_usd) AS cost_usd,"
            " SUM(cached) AS cache_hits, SUM(CASE WHEN status NOT IN ('ok', 'repaired', 'cached')"
            " THEN 1 ELSE 0 END) AS failures"
            " FROM llm_calls WHERE run_id = ? GROUP BY agent, provider, model ORDER BY MIN(id)",
            (run_id,),
        )
        totals = {
            k: sum((r[k] or 0) for r in rows)
            for k in ("calls", "input_tokens", "output_tokens", "latency_ms", "cost_usd", "cache_hits", "failures")
        }
        return {"by_agent": rows, "totals": totals}

    # ------------------------------------------------------------------ approvals
    def record_approval(
        self, run_id: str, gate: str, decision: str, actor: str = "human", comment: str = ""
    ) -> int:
        cur = self.execute(
            "INSERT INTO approvals (run_id, ts, gate, decision, actor, comment) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, utcnow_iso(), gate, decision, actor, comment),
        )
        return int(cur.lastrowid or 0)

    def approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM approvals WHERE run_id = ? ORDER BY id", (run_id,))

    # ------------------------------------------------------------------ llm cache
    def cache_get(self, key: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM llm_cache WHERE key = ?", (key,))
        return rows[0] if rows else None

    def cache_put(
        self, key: str, provider: str, model: str, response: str, input_tokens: int, output_tokens: int
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO llm_cache (key, provider, model, ts, response, input_tokens, output_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, provider, model, utcnow_iso(), response, input_tokens, output_tokens),
        )
