"""Tamper-evident audit log.

Each run has its own hash chain: entry_n.hash = sha256(entry_{n-1}.hash + canonical_json(entry_n)).
Editing, deleting or reordering any entry breaks every hash after it, which `verify` detects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from forgeline.store.db import Database, dumps, utcnow_iso

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def entry_hash(prev_hash: str, run_id: str, seq: int, ts: str, actor: str, action: str, details: str) -> str:
    body = canonical(
        {"run_id": run_id, "seq": seq, "ts": ts, "actor": actor, "action": action, "details": details}
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    entries: int
    broken_at_seq: int | None = None
    reason: str = ""


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(self, run_id: str, actor: str, action: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        details_json = dumps(details or {})
        with self.db.transaction() as conn:
            last = conn.execute(
                "SELECT seq, hash FROM audit_log WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)
            ).fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev_hash = last["hash"] if last else GENESIS
            ts = utcnow_iso()
            digest = entry_hash(prev_hash, run_id, seq, ts, actor, action, details_json)
            conn.execute(
                "INSERT INTO audit_log (run_id, seq, ts, actor, action, details, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, seq, ts, actor, action, details_json, prev_hash, digest),
            )
        return {"seq": seq, "ts": ts, "actor": actor, "action": action, "hash": digest}

    def entries(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM audit_log WHERE run_id = ? ORDER BY seq", (run_id,))
        for row in rows:
            row["details"] = json.loads(row["details"])
        return rows

    def verify(self, run_id: str) -> AuditVerification:
        rows = self.db.query("SELECT * FROM audit_log WHERE run_id = ? ORDER BY seq", (run_id,))
        prev = GENESIS
        for expected_seq, row in enumerate(rows, start=1):
            if row["seq"] != expected_seq:
                return AuditVerification(False, len(rows), row["seq"], f"sequence gap: expected {expected_seq}")
            if row["prev_hash"] != prev:
                return AuditVerification(False, len(rows), row["seq"], "previous-hash link broken")
            recomputed = entry_hash(
                prev, run_id, row["seq"], row["ts"], row["actor"], row["action"], row["details"]
            )
            if recomputed != row["hash"]:
                return AuditVerification(False, len(rows), row["seq"], "entry content was modified")
            prev = row["hash"]
        return AuditVerification(True, len(rows))
