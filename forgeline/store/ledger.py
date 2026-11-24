"""Action ledger: makes side effects idempotent.

LangGraph re-runs a node from its start when resuming after an interrupt, and a retried node may
attempt the same action again. Every side-effecting action (writing files, committing, merging,
deploying) first claims an idempotency key = sha256(run_id, action, inputs). A second claim with the
same key is refused, so the action is skipped instead of repeated.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from forgeline.store.audit import canonical
from forgeline.store.db import Database, dumps, utcnow_iso


class ActionLedger:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def key(run_id: str, action: str, inputs: Any) -> str:
        return hashlib.sha256(canonical({"run": run_id, "action": action, "inputs": inputs}).encode()).hexdigest()

    def claim(self, run_id: str, action: str, inputs: Any) -> tuple[bool, str]:
        """Return (True, key) if this action is new, (False, key) if it was already performed."""
        key = self.key(run_id, action, inputs)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO ledger (key, run_id, action, ts, inputs) VALUES (?, ?, ?, ?, ?)",
            (key, run_id, action, utcnow_iso(), dumps(inputs)),
        )
        return cur.rowcount == 1, key

    def complete(self, key: str, result: Any) -> None:
        self.db.execute("UPDATE ledger SET result = ? WHERE key = ?", (dumps(result), key))

    def result(self, key: str) -> Any:
        rows = self.db.query("SELECT result FROM ledger WHERE key = ?", (key,))
        if not rows or rows[0]["result"] is None:
            return None
        return json.loads(rows[0]["result"])

    def actions(self, run_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT key, action, ts, result FROM ledger WHERE run_id = ? ORDER BY ts", (run_id,))
