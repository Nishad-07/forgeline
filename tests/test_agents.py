"""Agent contracts: semantic checks and the repair loop, using the scripted MockBackend."""

from __future__ import annotations

import pytest

from forgeline.agents import (
    AgentOutputError,
    AnalystAgent,
    ArchitectAgent,
    DeveloperAgent,
    EvaluatorAgent,
    QAAgent,
    ReleaseAgent,
    SecurityAgent,
    acceptance_test_path,
    combine_scores,
)
from forgeline.agents.developer import EditError, apply_edits
from forgeline.config import ROOT
from forgeline.llm import LLMRouter, MockBackend
from forgeline.policy import Policy
from forgeline.requirements import Requirement
from forgeline.schemas import (
    AnalysisResult,
    Clarification,
    CodeChange,
    DesignProposal,
    RubricEvaluation,
    TextEdit,
)
from forgeline.tools.repo_index import index_repo
from forgeline.tools.security_scan import Finding

POLICY = Policy.load(ROOT / "policy.yaml")
REQ = Requirement.load(ROOT / "requirements" / "partial_refund.md")
INDEX = index_repo(ROOT / "sample_repo")

ANALYSIS = {
    "summary": "Support partial refunds.",
    "acceptance_criteria": [
        {"id": "AC1", "given": "a captured payment of 10000", "when": "refund 4000", "then": "refundable is 6000"},
        {"id": "AC2", "given": "refunded 7000", "when": "refund 3001", "then": "400 REFUND_EXCEEDS_REMAINING"},
        {"id": "AC3", "given": "a captured payment", "when": "refund without amount", "then": "full refund"},
    ],
    "tasks": [{"id": "T1", "title": "Track refunded amount", "description": "Add refunded_paise"}],
    "clarifying_questions": ["Notify merchants on every partial refund?"],
    "assumptions": [],
    "out_of_scope": [],
    "ambiguity_score": 0.6,
}
DESIGN = {
    "summary": "Track refunded amount on Payment.",
    "impacted_components": [
        {"path": "app/models.py", "change_type": "modify", "reason": "status + refunded_paise"},
        {"path": "app/refunds.py", "change_type": "modify", "reason": "partial refund logic"},
        {"path": "app/main.py", "change_type": "modify", "reason": "API field"},
    ],
    "context_files": ["app/errors.py"],
    "untouched_components": ["app/merchants.py", "app/notifications.py"],
    "design_notes": "x", "test_strategy": "y", "risks": [],
}
GOOD_TESTS = (
    "from fastapi.testclient import TestClient\nfrom app.main import app\n\n"
    "def test_a():\n    assert TestClient(app)\n\ndef test_b():\n    pass\n\ndef test_c():\n    pass\n"
)


def change_to_text(change: dict) -> str:
    """Render a CodeChange-shaped dict in the Developer's plain-text format."""
    out = [f"RATIONALE: {change.get('rationale', '')}"]
    if change.get("design_objection"):
        out.append(f"OBJECTION: {change['design_objection']}")
    for f in change.get("files", []):
        out.append(f"FILE: {f['path']}")
        for e in f.get("edits", []):
            out += ["<<<<<<< SEARCH", e["find"], "=======", e["replace"], ">>>>>>> REPLACE"]
        if f.get("content"):
            out += ["<<<<<<< CONTENT", f["content"].rstrip("\n"), ">>>>>>> END"]
    return "\n".join(out)


def suite_to_text(suite: dict) -> str:
    return f"COVERS: {', '.join(suite.get('covered_criteria', []))}\n```python\n{suite['content']}```\n"


TEXT_RENDERERS = {"CodeChange": change_to_text, "AcceptanceTestSuite": suite_to_text}


def router_with(**responses):
    """Mock router. CodeChange / AcceptanceTestSuite dicts are rendered into the agents' text protocol."""
    mapped = {}
    for key, items in responses.items():
        if key in TEXT_RENDERERS:
            mapped["TextResponse"] = [TEXT_RENDERERS[key](i) if isinstance(i, dict) else i for i in items]
        else:
            mapped[key] = list(items)
    backend = MockBackend(name="mock", responses=mapped)
    events = []
    router = LLMRouter({"mock": backend}, "mock", on_event=lambda r, kind, a, p: events.append((kind, a, p)),
                       sleep=lambda s: None)
    return router, backend, events


def test_requirement_front_matter():
    assert REQ.id == "REQ-101" and REQ.title == "Partial refunds" and REQ.priority == "high"
    assert "amount_paise" in REQ.body and not REQ.body.startswith("---")


# ------------------------------------------------------------------ analyst
def test_analyst_valid_output():
    router, backend, _ = router_with(AnalysisResult=[ANALYSIS])
    result = AnalystAgent(router, POLICY).run(requirement=REQ, repo_summary=INDEX.summary())
    assert len(result.acceptance_criteria) == 3 and result.clarifying_questions
    prompt = backend.calls[0].messages[1][1]
    assert "REFUND_EXCEEDS_REMAINING" in prompt and "app/refunds.py" in prompt


def test_analyst_semantic_repair_after_duplicate_ids():
    bad = {**ANALYSIS, "acceptance_criteria": [ANALYSIS["acceptance_criteria"][0]] * 3}
    router, backend, events = router_with(AnalysisResult=[bad, ANALYSIS])
    result = AnalystAgent(router, POLICY).run(requirement=REQ)
    assert len({c.id for c in result.acceptance_criteria}) == 3
    assert len(backend.calls) == 2
    assert "unique" in backend.calls[1].messages[-1][1]
    assert any(kind == "agent_output_rejected" for kind, _, _ in events)


def test_analyst_must_not_reask_after_clarification():
    answered = {**ANALYSIS, "clarifying_questions": []}
    router, backend, _ = router_with(AnalysisResult=[ANALYSIS, answered])
    clar = [Clarification(question="Notify merchants on every partial refund?", answer="Yes, every refund.")]
    result = AnalystAgent(router, POLICY).run(requirement=REQ, clarifications=clar)
    assert result.clarifying_questions == []
    assert "Yes, every refund." in backend.calls[0].messages[1][1]


def test_agent_gives_up_after_repair_budget():
    bad = {**ANALYSIS, "tasks": []}
    router, backend, _ = router_with(AnalysisResult=[bad])
    with pytest.raises(AgentOutputError) as exc:
        AnalystAgent(router, POLICY).run(requirement=REQ)
    assert len(backend.calls) == POLICY.autonomy.max_repair_attempts + 1
    assert "task" in exc.value.problems[0]


# ------------------------------------------------------------------ architect
def test_architect_valid_design_and_allowlist():
    router, *_ = router_with(DesignProposal=[DESIGN])
    design = ArchitectAgent(router, POLICY).run(requirement=REQ, analysis=AnalysisResult(**ANALYSIS), index=INDEX)
    assert design.allowlist() == {"app/models.py", "app/refunds.py", "app/main.py"}
    # models.py is imported by payments.py and store.py, which the design neither changes nor mentions
    warnings = ArchitectAgent.impact_warnings(design, INDEX)
    assert {w.split()[0] for w in warnings} == {"app/payments.py", "app/store.py"}


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ({"path": "app/refund_engine.py", "change_type": "modify", "reason": "r"}, "does not exist"),
        ({"path": "app/refunds.py", "change_type": "create", "reason": "r"}, "already exists"),
        ({"path": "pyproject.toml", "change_type": "modify", "reason": "r"}, "protected"),
    ],
)
def test_architect_rejects_bad_components(component, expected):
    bad = {**DESIGN, "impacted_components": [*DESIGN["impacted_components"][:1], component]}
    agent = ArchitectAgent(router_with()[0], POLICY)
    problems = agent.check(DesignProposal(**bad), index=INDEX)
    assert any(expected in p for p in problems), problems


def test_architect_impact_warning_for_missed_dependent():
    partial = {**DESIGN, "impacted_components": DESIGN["impacted_components"][1:2], "untouched_components": []}
    warnings = ArchitectAgent.impact_warnings(DesignProposal(**partial), INDEX)
    assert any("app/main.py imports app/refunds.py" in w for w in warnings)


def test_architect_reconcile_prompt_includes_objection():
    router, backend, _ = router_with(DesignProposal=[DESIGN])
    ArchitectAgent(router, POLICY).run(
        requirement=REQ, analysis=AnalysisResult(**ANALYSIS), index=INDEX,
        objection="app/errors.py needs a new error class", previous=DesignProposal(**DESIGN),
    )
    assert "app/errors.py needs a new error class" in backend.calls[0].messages[1][1]


# ------------------------------------------------------------------ developer
def _dev_inputs():
    return {
        "requirement": REQ, "analysis": AnalysisResult(**ANALYSIS), "design": DesignProposal(**DESIGN),
        "files": {"app/refunds.py": "x = 1\n"}, "context": {"app/errors.py": "class E: ...\n"},
    }


def test_developer_syntax_error_is_repaired():
    broken = {"files": [{"path": "app/refunds.py", "content": "def f(:\n  pass\n"}], "rationale": "r"}
    fixed = {"files": [{"path": "app/refunds.py", "content": "def f():\n    pass\n"}], "rationale": "r"}
    router, backend, _ = router_with(CodeChange=[broken, fixed])
    change = DeveloperAgent(router, POLICY).run(**_dev_inputs())
    assert change.files[0].content.startswith("def f()")
    assert "syntax error" in backend.calls[1].messages[-1][1]


def test_developer_rejects_abbreviated_file():
    lazy = {"files": [{"path": "app/refunds.py", "content": "x = 1\n# ... existing code ...\n"}], "rationale": "r"}
    problems = DeveloperAgent(router_with()[0], POLICY).check(
        DeveloperAgent.schema.model_validate(lazy))
    assert any("abbreviated" in p for p in problems)


def test_developer_objection_is_accepted_without_files():
    objection = {"files": [], "rationale": "r", "design_objection": "need app/errors.py"}
    router, *_ = router_with(CodeChange=[objection])
    change = DeveloperAgent(router, POLICY).run(**_dev_inputs())
    assert change.design_objection and not change.files


SOURCE = 'def refund(amount):\n    """Refund."""\n    return amount\n\n\ndef other():\n    return 1\n'


def test_apply_edits_exact_and_fuzzy():
    exact = apply_edits(SOURCE, [TextEdit(find="    return amount\n", replace="    return amount * 2\n")])
    assert "amount * 2" in exact and "def other" in exact
    # indentation differs from the file: matched line-by-line ignoring surrounding whitespace
    fuzzy = apply_edits(SOURCE, [TextEdit(find="def other():\n  return 1", replace="def other():\n    return 2")])
    assert "return 2" in fuzzy and "return amount\n" in fuzzy


def test_apply_edits_reports_missing_and_ambiguous():
    with pytest.raises(EditError, match="not found"):
        apply_edits(SOURCE, [TextEdit(find="def nope():", replace="x")], "app/x.py")
    with pytest.raises(EditError, match="matches 2 places"):
        apply_edits("a = 1\na = 1\n", [TextEdit(find="a = 1", replace="b")])
    with pytest.raises(EditError, match="empty"):
        apply_edits(SOURCE, [TextEdit(find="  ", replace="x")])


def test_developer_edits_are_resolved_into_files():
    edit = {"path": "app/refunds.py", "edits": [{"find": "    return amount", "replace": "    return amount - 1"}]}
    change = {"files": [edit, {"path": "app/new.py", "content": "X = 1\n"}], "rationale": "r"}
    router, *_ = router_with(CodeChange=[change])
    agent = DeveloperAgent(router, POLICY, mode="edits")
    inputs = {**_dev_inputs(), "files": {"app/refunds.py": SOURCE, "app/new.py": None}}
    result = agent.run(**inputs)
    writes = {w.path: w.content for w in agent.resolve(result, inputs["files"])}
    assert "amount - 1" in writes["app/refunds.py"] and writes["app/new.py"] == "X = 1\n"


def test_developer_edit_that_breaks_syntax_is_repaired():
    bad = {"files": [{"path": "app/refunds.py", "edits": [{"find": "def other():", "replace": "def other(:"}]}],
           "rationale": "r"}
    good = {"files": [{"path": "app/refunds.py", "edits": [{"find": "return 1", "replace": "return 3"}]}],
            "rationale": "r"}
    router, backend, _ = router_with(CodeChange=[bad, good])
    inputs = {**_dev_inputs(), "files": {"app/refunds.py": SOURCE}}
    DeveloperAgent(router, POLICY).run(**inputs)
    assert "syntax error" in backend.calls[1].messages[-1][1]
    assert backend.calls[0].temperature is None and backend.calls[1].temperature == 0.4


def test_developer_resolve_reads_out_of_scope_file_for_scope_guard(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "merchants.py").write_text("A = 1\n")
    change = CodeChange.model_validate(
        {"files": [{"path": "app/merchants.py", "edits": [{"find": "A = 1", "replace": "A = 2"}]}], "rationale": "r"})
    agent = DeveloperAgent(router_with()[0], POLICY)
    assert agent.check(change, files={"app/refunds.py": SOURCE}) == []  # left for the scope guard to judge
    writes = agent.resolve(change, {"app/refunds.py": SOURCE}, repo_root=tmp_path)
    assert writes[0].path == "app/merchants.py" and writes[0].content == "A = 2\n"


def test_full_mode_rejects_edits():
    change = CodeChange.model_validate(
        {"files": [{"path": "app/refunds.py", "edits": [{"find": "return 1", "replace": "return 3"}]}],
         "rationale": "r"})
    problems = DeveloperAgent(router_with()[0], POLICY, mode="full").check(change, files={"app/refunds.py": SOURCE})
    assert any("full mode" in p for p in problems)


def test_agent_escalates_to_next_provider_when_repairs_run_out():
    bad = {**ANALYSIS, "tasks": []}
    weak = MockBackend(name="ollama", responses={"AnalysisResult": [bad]})
    strong = MockBackend(name="gemini", responses={"AnalysisResult": [ANALYSIS]})
    events = []
    router = LLMRouter({"ollama": weak, "gemini": strong}, "ollama",
                       on_event=lambda r, kind, a, p: events.append((kind, p)), sleep=lambda s: None)
    result = AnalystAgent(router, POLICY).run(requirement=REQ)
    assert result.tasks
    assert len(weak.calls) == POLICY.autonomy.max_repair_attempts + 1 and len(strong.calls) == 1
    escalation = [p for kind, p in events if kind == "agent_escalation"]
    assert escalation == [{"from": "ollama", "to": "gemini", "problems": escalation[0]["problems"]}]
    # the stronger model starts fresh, without the weaker model's rejected answers
    assert len(strong.calls[0].messages) == 2


def test_developer_prompt_contains_feedback():
    ok = {"files": [{"path": "app/refunds.py", "content": "x = 2\n"}], "rationale": "r"}
    router, backend, _ = router_with(CodeChange=[ok])
    DeveloperAgent(router, POLICY).run(**_dev_inputs(), feedback=["FAILED test_exact_remaining"], attempt=2)
    prompt = backend.calls[0].messages[1][1]
    assert "Attempt 2" in prompt and "test_exact_remaining" in prompt and "class E" in prompt


# ------------------------------------------------------------------ QA
def test_qa_accepts_valid_suite():
    suite = {"content": GOOD_TESTS, "covered_criteria": ["AC1", "AC2"], "notes": ""}
    router, *_ = router_with(AcceptanceTestSuite=[suite])
    result = QAAgent(router, POLICY).run(
        requirement=REQ, analysis=AnalysisResult(**ANALYSIS), repo_summary="", example_tests={})
    assert "def test_a" in result.content
    assert acceptance_test_path("REQ-101") == "tests/test_acceptance_req_101.py"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("import os\n" + GOOD_TESTS, "not allowed"),
        ("def test_a():\n    pass\n", "only 1 test"),
        ("def test_a(:\n", "syntax error"),
        ("from tests.golden.x import y\n" + GOOD_TESTS, "golden"),
    ],
)
def test_qa_rejects_unsafe_or_weak_suites(content, expected):
    suite = QAAgent.schema.model_validate({"content": content, "covered_criteria": ["AC1"]})
    problems = QAAgent(router_with()[0], POLICY).check(suite, analysis=AnalysisResult(**ANALYSIS))
    assert any(expected in p for p in problems), problems


# ------------------------------------------------------------------ security / release / evaluator
def test_security_must_assess_each_finding():
    finding = Finding("PAN_IN_LOG", "critical", "app/refunds.py", 30, "card in logs", introduced=True)
    partial = {"assessments": [], "additional_concerns": [], "summary": "ok"}
    full = {"assessments": [{"rule": "PAN_IN_LOG", "file": "app/refunds.py", "line": 30,
                             "verdict": "confirmed", "explanation": "PAN logged"}],
            "additional_concerns": [], "summary": "PAN logged"}
    router, backend, _ = router_with(SecurityTriage=[partial, full])
    triage = SecurityAgent(router, POLICY).run(requirement=REQ, diff="+ logger.info(card_number)", findings=[finding])
    assert triage.assessments[0].verdict == "confirmed"
    assert "CRITICAL" in backend.calls[0].messages[1][1]


def test_release_cannot_approve_failing_build():
    summary = {
        "headline": "h", "change_summary": "c",
        "criteria_evidence": [{"criterion_id": f"AC{i}", "status": "met", "evidence": "t"} for i in (1, 2, 3)],
        "known_risks": [], "rollback_plan": "revert", "recommendation": "approve", "recommendation_reason": "r",
    }
    agent = ReleaseAgent(router_with()[0], POLICY)
    model = ReleaseAgent.schema.model_validate(summary)
    analysis = AnalysisResult(**ANALYSIS)
    assert agent.check(model, analysis=analysis, facts={"all_tests_passed": True, "blocking_security_findings": 0}) == []
    problems = agent.check(model, analysis=analysis, facts={"all_tests_passed": False})
    assert any("contradicts" in p for p in problems)
    missing = ReleaseAgent.schema.model_validate({**summary, "criteria_evidence": summary["criteria_evidence"][:1]})
    assert any("missing criteria" in p for p in agent.check(missing, analysis=analysis,
                                                            facts={"all_tests_passed": True}))


def test_evaluator_requires_full_rubric_and_scores_combine():
    items = [{"criterion": c, "score": 4, "justification": "j"} for c in
             ("correctness", "scope_discipline", "code_quality", "test_quality", "security")]
    full = RubricEvaluation(items=items, overall_comment="good")
    agent = EvaluatorAgent(router_with()[0], POLICY)
    assert agent.check(full) == []
    assert agent.check(RubricEvaluation(items=items[:3], overall_comment="x"))
    scores = combine_scores(full, golden_passed=12, golden_total=12)
    assert scores == {"golden_pass_rate": 100.0, "rubric_percent": 80.0, "overall": 92.0}
    assert combine_scores(None, 6, 12)["overall"] == 50.0


# ------------------------------------------------------------------ plain-text protocol parsing
def test_parse_change_handles_quotes_and_multiple_files():
    from forgeline.agents.change_format import parse_change

    text = (
        "RATIONALE: track refunds\n"
        "FILE: app/refunds.py\n"
        "<<<<<<< SEARCH\n"
        "    return amount\n"
        "=======\n"
        '    return f"{payment["id"]}: {amount}"  # quotes, braces and \\ backslashes survive\n'
        ">>>>>>> REPLACE\n"
        "FILE: `app/new.py`\n"
        "<<<<<<< CONTENT\n"
        "```python\n"
        'X = "a"\n'
        "```\n"
        ">>>>>>> END\n"
    )
    change = parse_change(text)
    assert change.rationale == "track refunds"
    assert change.files[0].edits[0].replace.startswith('    return f"{payment["id"]}')
    assert change.files[1].path == "app/new.py" and change.files[1].content == 'X = "a"\n'


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE", "before any 'FILE"),
        ("FILE: app/a.py\n<<<<<<< SEARCH\nx\n", "no ======= divider"),
        ("FILE: app/a.py\n<<<<<<< SEARCH\nx\n=======\ny\n", "no >>>>>>> REPLACE"),
        ("Sure, here is my answer.", "no changes found"),
    ],
)
def test_parse_change_errors(text, error):
    from forgeline.agents.change_format import parse_change

    with pytest.raises(ValueError, match=error):
        parse_change(text)


def test_parse_change_objection_only():
    from forgeline.agents.change_format import parse_change

    change = parse_change("OBJECTION: app/errors.py must change\nbut it is not in the design")
    assert not change.files and "not in the design" in change.design_objection


def test_bad_text_format_is_repaired():
    router, backend, _ = router_with(CodeChange=["I changed the refund logic.", {
        "files": [{"path": "app/refunds.py", "edits": [{"find": "return 1", "replace": "return 3"}]}],
        "rationale": "r"}])
    inputs = {**_dev_inputs(), "files": {"app/refunds.py": SOURCE}}
    change = DeveloperAgent(router, POLICY).run(**inputs)
    assert change.files[0].edits[0].replace == "return 3"
    assert "output format" in backend.calls[1].messages[-1][1]


def test_qa_parses_fenced_block_with_quotes():
    text = 'COVERS: AC1, AC2\n```python\ndef test_x(client):\n    assert client["id"] == "a"\n```'
    suite = QAAgent(router_with()[0], POLICY).parse(text)
    assert suite.covered_criteria == ["AC1", "AC2"] and 'client["id"]' in suite.content
    with pytest.raises(ValueError):
        QAAgent(router_with()[0], POLICY).parse("I wrote some tests.")


def test_qa_rejects_imported_fixtures():
    conftest = "import pytest\n\n@pytest.fixture\ndef client():\n    return 1\n\n@pytest.fixture\ndef merchant():\n    return 2\n"
    bad = "from tests.conftest import client\n" + GOOD_TESTS
    suite = QAAgent.schema.model_validate({"content": bad, "covered_criteria": ["AC1"]})
    problems = QAAgent(router_with()[0], POLICY).check(
        suite, analysis=AnalysisResult(**ANALYSIS), example_tests={"tests/conftest.py": conftest})
    assert any("request them as test-function parameters" in p for p in problems)


def test_broken_test_reasons_distinguish_missing_feature_from_broken_test():
    from forgeline.agents.qa import broken_test_reasons

    assert broken_test_reasons(["AttributeError: 'FixtureFunctionDefinition' object has no attribute 'post'"])
    assert broken_test_reasons(["NameError: name 'helper' is not defined"])
    # legitimate pre-change failures: the feature is missing
    assert broken_test_reasons(["assert 201 == 400", "KeyError: 'refundable_paise'",
                                "AttributeError: 'Payment' object has no attribute 'refunded_paise'",
                                "TypeError: refund_payment() got an unexpected keyword argument 'amount_paise'"]) == []
