from __future__ import annotations

import pytest

from forgeline.store import ActionLedger, AuditLog


def test_run_lifecycle(db):
    db.create_run("run1", requirement_id="REQ-101", title="Partial refunds", provider="gemini")
    db.update_run("run1", status="running", stage="analyst")
    db.update_run("run1", attempts=2)
    run = db.get_run("run1")
    assert run["status"] == "running"
    assert run["data"] == {"stage": "analyst", "attempts": 2}
    assert [r["id"] for r in db.list_runs()] == ["run1"]


def test_update_missing_run(db):
    with pytest.raises(KeyError):
        db.update_run("nope", status="x")


def test_events_are_incremental(db):
    db.create_run("run1")
    first = db.add_event("run1", "node_start", node="analyst")
    db.add_event("run1", "node_end", node="analyst", duration_ms=12)
    assert [e["kind"] for e in db.events("run1")] == ["node_start", "node_end"]
    newer = db.events("run1", after_id=first)
    assert len(newer) == 1 and newer[0]["payload"] == {"duration_ms": 12}


def test_usage_summary(db):
    db.record_llm_call(run_id="r", agent="analyst", provider="gemini", model="m", input_tokens=100,
                       output_tokens=50, latency_ms=10, cost_usd=0.001, cached=0, status="ok")
    db.record_llm_call(run_id="r", agent="analyst", provider="gemini", model="m", input_tokens=0,
                       output_tokens=0, latency_ms=5, cost_usd=0, cached=0, status="provider_error")
    summary = db.usage_summary("r")
    assert summary["totals"]["calls"] == 2
    assert summary["totals"]["input_tokens"] == 100
    assert summary["totals"]["failures"] == 1


def test_audit_chain_verifies(db):
    audit = AuditLog(db)
    for i in range(5):
        audit.append("run1", "agent:developer", "write_file", {"path": f"app/f{i}.py"})
    audit.append("run2", "human", "approve", {})
    result = audit.verify("run1")
    assert result.ok and result.entries == 5
    assert audit.verify("run2").ok


def test_audit_detects_modified_entry(db):
    audit = AuditLog(db)
    for i in range(4):
        audit.append("run1", "agent", "step", {"i": i})
    db.execute("UPDATE audit_log SET details = '{\"i\": 99}' WHERE run_id = 'run1' AND seq = 2")
    result = audit.verify("run1")
    assert not result.ok
    assert result.broken_at_seq == 2
    assert "modified" in result.reason


def test_audit_detects_deleted_entry(db):
    audit = AuditLog(db)
    for i in range(4):
        audit.append("run1", "agent", "step", {"i": i})
    db.execute("DELETE FROM audit_log WHERE run_id = 'run1' AND seq = 3")
    result = audit.verify("run1")
    assert not result.ok and result.broken_at_seq == 4


def test_ledger_blocks_repeated_action(db):
    ledger = ActionLedger(db)
    first, key = ledger.claim("run1", "write_files", {"files": ["app/refunds.py"], "attempt": 1})
    again, key2 = ledger.claim("run1", "write_files", {"attempt": 1, "files": ["app/refunds.py"]})
    assert first is True and again is False and key == key2
    ledger.complete(key, {"written": 1})
    assert ledger.result(key) == {"written": 1}
    other, _ = ledger.claim("run1", "write_files", {"files": ["app/refunds.py"], "attempt": 2})
    assert other is True
