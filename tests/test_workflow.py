"""End-to-end workflow tests: the real LangGraph graph, git worktrees, pytest, scope guard and scanners,
driven by a scripted LLM. One test per failure scenario from the problem statement, plus the happy path.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from forgeline.config import ROOT, Settings
from forgeline.llm import LLMRouter, MockBackend
from forgeline.orchestrator import GateError, Orchestrator
from forgeline.store import AuditLog

REFERENCE = ROOT / "baseline" / "reference"
RUBRIC = {"items": [{"criterion": c, "score": 4, "justification": "ok"} for c in
                    ("correctness", "scope_discipline", "code_quality", "test_quality", "security")],
          "overall_comment": "solid"}


def analysis(questions: list[str] | None = None, ids=("AC1", "AC2", "AC3")) -> dict:
    return {
        "summary": "Partial refunds.",
        "acceptance_criteria": [{"id": i, "given": "g", "when": "w", "then": "t"} for i in ids],
        "tasks": [{"id": "T1", "title": "Implement", "description": "d"}],
        "clarifying_questions": questions or [], "assumptions": [], "out_of_scope": [],
        "ambiguity_score": 0.6 if questions else 0.1,
    }


def design(paths=("app/models.py", "app/refunds.py", "app/main.py")) -> dict:
    return {
        "summary": "Track refunded amount.",
        "impacted_components": [{"path": p, "change_type": "modify", "reason": "r"} for p in paths],
        "context_files": ["app/errors.py"], "untouched_components": ["app/merchants.py"],
        "design_notes": "n", "test_strategy": "t", "risks": ["over-refund"],
    }


def release(ids=("AC1", "AC2", "AC3"), recommendation="approve") -> dict:
    return {
        "headline": "Partial refunds", "change_summary": "c",
        "criteria_evidence": [{"criterion_id": i, "status": "met", "evidence": "tests"} for i in ids],
        "known_risks": [], "rollback_plan": "revert merge commit", "recommendation": recommendation,
        "recommendation_reason": "all checks pass",
    }


def full_files(req: str, **overrides: tuple[str, str]) -> str:
    """Developer answer with CONTENT blocks for every file of a reference solution, optionally mutated."""
    out = ["RATIONALE: implement the requirement"]
    for path in sorted((REFERENCE / req).rglob("*.py")):
        rel = path.relative_to(REFERENCE / req).as_posix()
        content = path.read_text(encoding="utf-8")
        if rel in overrides:
            old, new = overrides[rel]
            assert old in content
            content = content.replace(old, new)
        out += [f"FILE: {rel}", "<<<<<<< CONTENT", content.rstrip("\n"), ">>>>>>> END"]
    return "\n".join(out)


BUGGY_101 = full_files("REQ-101", **{"app/refunds.py": ("if amount_paise > remaining:", "if amount_paise >= remaining:")})
GOOD_101 = full_files("REQ-101")
LEAKY_102 = full_files("REQ-102", **{"app/refunds.py": ("payment.masked_card,  #", "payment.card_number,  #")})
GOOD_102 = full_files("REQ-102")

ACCEPTANCE_101 = '''COVERS: AC1, AC2, AC3
```python
from __future__ import annotations


def _captured(client, amount=10_000):
    merchant = client.post("/merchants", json={"name": "Shop"}).json()
    payment = client.post(
        "/payments", json={"merchant_id": merchant["id"], "amount_paise": amount, "card_number": "4111111111111111"}
    ).json()
    return client.post(f"/payments/{payment['id']}/capture").json()


def test_partial_refund_updates_balance(client):
    p = _captured(client)
    assert client.post(f"/payments/{p['id']}/refunds", json={"amount_paise": 4000}).status_code == 201
    body = client.get(f"/payments/{p['id']}").json()
    assert body["status"] == "PARTIALLY_REFUNDED" and body["refundable_paise"] == 6000


def test_exact_remaining_marks_refunded(client):
    p = _captured(client)
    client.post(f"/payments/{p['id']}/refunds", json={"amount_paise": 6000})
    assert client.post(f"/payments/{p['id']}/refunds", json={"amount_paise": 4000}).status_code == 201
    assert client.get(f"/payments/{p['id']}").json()["status"] == "REFUNDED"


def test_over_refund_rejected(client):
    p = _captured(client)
    r = client.post(f"/payments/{p['id']}/refunds", json={"amount_paise": 10001})
    assert r.status_code == 400 and r.json()["error"] == "REFUND_EXCEEDS_REMAINING"
```
'''

ACCEPTANCE_102 = '''COVERS: AC1, AC2, AC3
```python
import logging

from app.merchants import create_merchant
from app.payments import capture_payment, create_payment
from app.refunds import refund_payment


def test_audit_entry_written(caplog):
    m = create_merchant("Shop")
    p = capture_payment(create_payment(m.id, 5000, "4111111111111111").id)
    with caplog.at_level(logging.INFO, logger="paylite.audit"):
        refund_payment(p.id, reason="dispute")
    assert [r for r in caplog.records if r.name == "paylite.audit"]


def test_audit_mentions_payment(caplog):
    m = create_merchant("Shop")
    p = capture_payment(create_payment(m.id, 5000, "4111111111111111").id)
    with caplog.at_level(logging.INFO, logger="paylite.audit"):
        refund_payment(p.id)
    assert any(p.id in r.getMessage() for r in caplog.records if r.name == "paylite.audit")


def test_refund_still_works():
    m = create_merchant("Shop")
    p = capture_payment(create_payment(m.id, 5000, "4111111111111111").id)
    assert refund_payment(p.id).amount_paise == 5000
```
'''

ROLES = {
    "Analyst agent": "analyst", "Architect agent": "architect", "QA agent": "qa", "Developer agent": "developer",
    "Security agent": "security", "Release agent": "release", "Evaluator": "evaluator",
}


def triage(schema, messages):
    findings = [ln for ln in messages[1][1].splitlines() if ln.startswith("- [")]
    return {"assessments": [{"rule": "R", "file": "f", "line": 1, "verdict": "confirmed", "explanation": "x"}
                            for _ in findings], "additional_concerns": [], "summary": "reviewed"}


class ScriptedLLM:
    """Dispatches on the agent role found in the system prompt; per-role queues (last item repeats)."""

    def __init__(self, **queues):
        defaults = {"security": [triage], "release": [release()], "evaluator": [RUBRIC]}
        self.queues = {**defaults, **{k: list(v) for k, v in queues.items()}}
        self.calls: dict[str, int] = {}
        self.outage_roles: set[str] = set()

    def __call__(self, schema, messages):
        system = messages[0][1]
        role = next(r for marker, r in ROLES.items() if marker in system)
        self.calls[role] = self.calls.get(role, 0) + 1
        if role in self.outage_roles:
            return httpx.ConnectError("connection refused")
        queue = self.queues[role]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item(schema, messages) if callable(item) else item


@pytest.fixture
def make_orch(tmp_path):
    def factory(script: ScriptedLLM) -> Orchestrator:
        settings = Settings.from_env().with_overrides(
            data_dir=tmp_path / "data", workspace_dir=tmp_path / "workspace", cache_enabled=False,
            dev_output_mode="edits",
        )
        ollama = MockBackend(name="ollama", default=script)
        gemini = MockBackend(name="gemini", default=script)

        def router_factory(provider, cache, db):
            return LLMRouter({"ollama": ollama, "gemini": gemini}, provider, db=db, cache_enabled=False,
                             sleep=lambda s: None)

        return Orchestrator(settings, router_factory=router_factory)

    return factory


def events(orch, run_id):
    return [e["kind"] for e in orch.db.events(run_id)]


def nodes_run(orch, run_id):
    return [e["node"] for e in orch.db.events(run_id) if e["kind"] == "node_end"]


# ---------------------------------------------------------------------------- minimum demonstration
def test_happy_path_with_failed_check_clarification_and_approval(make_orch):
    script = ScriptedLLM(
        analyst=[analysis(["Notify on every partial refund?"]), analysis()],
        architect=[design()], qa=[ACCEPTANCE_101], developer=[BUGGY_101, GOOD_101],
    )
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["status"] == "awaiting_human" and status["gate"]["gate"] == "clarification"
    assert status["gate"]["questions"] == ["Notify on every partial refund?"]

    status = orch.resume(status["id"], {"answers": ["Yes, every refund."], "actor": "pm"})
    assert status["gate"]["gate"] == "release_approval", status
    run_id = status["id"]
    state = orch.state(run_id)["values"]

    # failed check -> feedback -> fixed on attempt 2
    log = state["attempts_log"]
    failed_per_attempt = [a["failed"] for a in log if a["stage"] == "qa"]
    assert len(failed_per_attempt) == 2 and failed_per_attempt[0] > 0 and failed_per_attempt[1] == 0
    assert state["dev_attempt"] == 2
    assert state["acceptance"]["baseline"]["failed"] >= 2  # tests fail before the change (test-first)
    assert state["evaluation"]["golden"]["passed"] == state["evaluation"]["golden"]["total"] == 12
    assert state["clarifications"][0]["answer"] == "Yes, every refund."

    # nothing reaches main before the human approves
    main_refunds = Path(orch.repo.path) / "app" / "refunds.py"
    assert "PARTIALLY_REFUNDED" not in main_refunds.read_text()
    with pytest.raises(GateError):
        orch.resume(run_id, {"decision": "ship it"})

    status = orch.resume(run_id, {"decision": "approve", "actor": "asha", "comment": "LGTM"})
    assert status["status"] == "completed"
    assert "PARTIALLY_REFUNDED" in main_refunds.read_text()
    deployments = (orch.settings.workspace_dir / "deployments.jsonl").read_text().splitlines()
    assert json.loads(deployments[-1])["approved_by"] == "asha"
    assert AuditLog(orch.db).verify(run_id).ok
    actions = [e["action"] for e in AuditLog(orch.db).entries(run_id)]
    for expected in ("run_started", "clarification_answered", "files_written", "checks_run", "release_decision",
                     "merged_to_main", "deployed_mock"):
        assert expected in actions
    assert orch.db.usage_summary(run_id)["totals"]["calls"] >= 8


# ---------------------------------------------------------------------------- failure scenarios
def test_developer_touching_unrelated_file_is_rejected(make_orch):
    sneaky = "FILE: app/merchants.py\n<<<<<<< SEARCH\ndef create_merchant(name: str) -> Merchant:\n=======\n" \
             "def create_merchant(name: str) -> Merchant:  # sneaky\n>>>>>>> REPLACE"
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101],
                         developer=[sneaky, GOOD_101])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["gate"] == "release_approval"
    assert "scope_violation" in events(orch, status["id"])
    state = orch.state(status["id"])["values"]
    assert state["attempts_log"][0]["scope_ok"] is False
    assert "merchants.py" not in state["evaluation"]["agent_files"]
    assert "# sneaky" not in (Path(orch.repo.path).parent / "runs" / status["id"] / "repo" / "app" /
                              "merchants.py").read_text()


def test_repeated_identical_change_is_blocked(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101],
                         developer=[BUGGY_101, BUGGY_101, GOOD_101])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["gate"] == "release_approval"
    assert "repeated_action_blocked" in events(orch, status["id"])


def test_critical_security_finding_blocks_then_autofix(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design(("app/refunds.py",))], qa=[ACCEPTANCE_102],
                         developer=[LEAKY_102, GOOD_102])
    orch = make_orch(script)
    status = orch.start("requirements/refund_audit_log.md", provider="ollama")
    assert status["gate"]["gate"] == "release_approval", status
    state = orch.state(status["id"])["values"]
    assert state["security_fix_attempts"] == 1
    assert state["security"]["blocking"] == []
    security_events = [e for e in orch.db.events(status["id"]) if e["node"] == "security" and e["kind"] == "node_end"]
    assert "blocking" in security_events[0]["payload"]["summary"]
    assert "card_number" not in (Path(orch.repo.path).parent / "runs" / status["id"] / "repo" / "app" /
                                 "refunds.py").read_text().split("audit_logger")[1]


def test_unfixed_security_finding_escalates_and_override_needs_justification(make_orch):
    still_leaky = LEAKY_102.replace('"refund_audit refund=', '"refund_audit v2 refund=')  # "fix" that still leaks
    script = ScriptedLLM(analyst=[analysis()], architect=[design(("app/refunds.py",))], qa=[ACCEPTANCE_102],
                         developer=[LEAKY_102, still_leaky])
    orch = make_orch(script)
    status = orch.start("requirements/refund_audit_log.md", provider="ollama")
    assert status["gate"]["gate"] == "escalation" and status["gate"]["reason"] == "security_block"
    assert "PAN_IN_LOG" in status["gate"]["details"]
    with pytest.raises(GateError, match="justification"):
        orch.resume(status["id"], {"decision": "override"})
    status = orch.resume(status["id"], {"decision": "override", "comment": "accepted for test env", "actor": "ciso"})
    assert status["gate"]["gate"] == "release_approval"
    status = orch.resume(status["id"], {"decision": "reject", "actor": "asha", "comment": "no PAN in logs"})
    assert status["status"] == "rejected"
    assert "PARTIALLY" not in (Path(orch.repo.path) / "app" / "refunds.py").read_text()
    assert "paylite.audit" not in (Path(orch.repo.path) / "app" / "refunds.py").read_text()


def test_provider_outage_pauses_and_resumes_from_checkpoint(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101], developer=[GOOD_101])
    script.outage_roles = {"architect"}
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="gemini")
    assert status["status"] == "paused_provider_unavailable"
    assert "llm_failover" in events(orch, status["id"]) and "run_paused" in events(orch, status["id"])
    assert script.calls["analyst"] == 1

    script.outage_roles.clear()  # provider is back
    status = orch.resume(status["id"], provider="ollama")
    assert status["gate"]["gate"] == "release_approval"
    assert script.calls["analyst"] == 1  # resumed from the checkpoint: analysis was not redone
    assert "provider_switched" in events(orch, status["id"])


def test_architect_developer_disagreement_reconciles(make_orch):
    script = ScriptedLLM(
        analyst=[analysis()], architect=[design(("app/refunds.py", "app/main.py")), design()],
        qa=[ACCEPTANCE_101],
        developer=["OBJECTION: app/models.py must change to add PARTIALLY_REFUNDED, but it is not in the design",
                   GOOD_101],
    )
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["gate"] == "release_approval"
    ran = nodes_run(orch, status["id"])
    assert ran.count("architect") == 2 and "reconcile" in ran
    assert "app/models.py" in orch.state(status["id"])["values"]["design"]["impacted_components"][0]["path"] or any(
        c["path"] == "app/models.py" for c in orch.state(status["id"])["values"]["design"]["impacted_components"])


def test_persistent_disagreement_escalates_to_human(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design(("app/refunds.py",))], qa=[ACCEPTANCE_101],
                         developer=["OBJECTION: this design cannot work"])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["reason"] == "design_deadlock"
    status = orch.resume(status["id"], {"decision": "abort", "actor": "lead"})
    assert status["status"] == "aborted"


def test_tests_keep_failing_then_human_grants_retry(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101],
                         developer=[BUGGY_101, BUGGY_101.replace("RATIONALE: implement", "RATIONALE: again"),
                                    BUGGY_101.replace("RATIONALE: implement", "RATIONALE: third"), GOOD_101])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["reason"] == "retry_budget_exhausted"
    assert orch.state(status["id"])["values"]["dev_attempt"] == 3
    status = orch.resume(status["id"], {"decision": "retry", "comment": "check the >= comparison", "actor": "lead"})
    assert status["gate"]["gate"] == "release_approval"
    assert script.calls["developer"] >= 4


def test_malformed_output_escalates_instead_of_continuing(make_orch):
    script = ScriptedLLM(analyst=["this is not json at all"])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["gate"]["reason"] == "malformed_output"
    assert status["gate"]["resume_node"] == "analyst"
    assert orch.resume(status["id"], {"decision": "abort"})["status"] == "aborted"


def test_request_changes_loops_back_to_developer(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101],
                         developer=[GOOD_101, GOOD_101.replace("Refund part or all", "Refund part or all (v2)")])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    with pytest.raises(GateError, match="comment"):
        orch.resume(status["id"], {"decision": "request_changes"})
    status = orch.resume(status["id"], {"decision": "request_changes", "comment": "tweak the docstring"})
    assert status["gate"]["gate"] == "release_approval"
    status = orch.resume(status["id"], {"decision": "approve", "actor": "asha"})
    assert status["status"] == "completed"
    assert "(v2)" in (Path(orch.repo.path) / "app" / "refunds.py").read_text()


# ---------------------------------------------------------------------------- live streaming + free text
def test_free_text_requirement_streams_agent_output(make_orch):
    script = ScriptedLLM(analyst=[analysis()], architect=[design()], qa=[ACCEPTANCE_101], developer=[GOOD_101])
    orch = make_orch(script)
    run_id = orch.create(requirement_text="Merchants want partial refunds on captured payments.\n\n"
                                          "Total refunded must never exceed the captured amount.",
                         provider="ollama")
    run = orch.db.get_run(run_id)
    assert run["requirement_id"].startswith("REQ-") and run["title"].startswith("Merchants want partial")
    status = orch.drive(run_id)
    assert status["gate"]["gate"] == "release_approval"
    events = orch.db.events(run_id)
    deltas = [e for e in events if e["kind"] == "llm_delta" and e["node"] == "analyst"]
    outputs = {e["node"]: e["payload"]["output"] for e in events if e["kind"] == "agent_output"}
    assert deltas, "analyst tokens were not streamed"
    streamed = "".join(e["payload"]["text"] for e in deltas if e["payload"]["call"] == deltas[0]["payload"]["call"])
    assert '"acceptance_criteria"' in streamed
    assert {"analyst", "architect", "qa", "developer", "security", "release", "evaluator"} <= set(outputs)
    assert outputs["developer"]["files"][0]["path"].startswith("app/")
    evaluation = orch.state(run_id)["values"]["evaluation"]
    assert evaluation["golden"]["available"] is False  # no hidden baseline for free text
    assert evaluation["scores"]["overall"] == evaluation["scores"]["rubric_percent"] == 80.0  # rubric only


def test_free_text_rejects_empty_requirement(make_orch):
    orch = make_orch(ScriptedLLM())
    with pytest.raises(ValueError, match="too short"):
        orch.create(requirement_text="  fix  ", provider="ollama")


# ---------------------------------------------------------------------------- stopping runs
def test_stop_run_waiting_at_a_gate(make_orch):
    script = ScriptedLLM(analyst=[analysis(["Notify on every partial refund?"])])
    orch = make_orch(script)
    status = orch.start("requirements/partial_refund.md", provider="ollama")
    assert status["status"] == "awaiting_human"
    stopped = orch.cancel(status["id"], actor="asha")
    assert stopped["status"] == "aborted" and stopped["outcome"] == "cancelled" and stopped["gate"] is None
    assert "run_cancelled" in events(orch, status["id"])
    assert AuditLog(orch.db).verify(status["id"]).ok
    with pytest.raises(GateError, match="already aborted"):
        orch.cancel(status["id"])
    with pytest.raises(GateError):
        orch.resume(status["id"], {"answers": ["x"]})


def test_stop_run_while_it_is_executing(make_orch):
    holder: dict = {}

    def analyst_then_stop(schema, messages):
        orch = holder["orch"]
        orch.cancel(holder["run_id"], actor="asha")  # a human presses Stop during the analyst call
        return analysis()

    script = ScriptedLLM(analyst=[analyst_then_stop], architect=[design()], qa=[ACCEPTANCE_101],
                         developer=[GOOD_101])
    orch = make_orch(script)
    holder["orch"] = orch
    run_id = orch.create("requirements/partial_refund.md", provider="ollama")
    holder["run_id"] = run_id
    status = orch.drive(run_id)
    assert status["status"] == "aborted" and status["outcome"] == "cancelled"
    assert "architect" not in script.calls  # stopped after the step in progress; nothing further ran
    assert orch.db.get_run(run_id)["status"] == "aborted"
