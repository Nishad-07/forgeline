"""Workflow nodes. Each node reads the persisted state, does one job, and returns a state update.

Conventions
* Side effects (file writes, commits, merges, deploys) go through the ActionLedger, so a node that
  LangGraph re-runs (after an interrupt or a crash) never repeats them.
* Nodes never decide routing directly; they record facts (e.g. `escalation`, `scope.ok`) and the
  routers in build.py choose the next node.
* Human gates use `interrupt()`. Code before the interrupt is free of side effects because it re-runs
  when the graph resumes.
* Budgets (dev attempts, reconcile rounds, security fixes, change requests) come from policy.yaml.
  When one is exhausted the node raises an escalation instead of looping.
"""

from __future__ import annotations

import functools
import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

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
from forgeline.agents.developer import EditError
from forgeline.agents.qa import broken_test_reasons
from forgeline.config import Settings
from forgeline.graph.state import WorkflowState
from forgeline.llm.errors import StructuredOutputError
from forgeline.llm.router import LLMRouter
from forgeline.policy import Policy
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, Clarification, DesignProposal
from forgeline.store import ActionLedger, AuditLog, Database
from forgeline.store.db import utcnow_iso
from forgeline.tools.quality import run_pytest, run_ruff
from forgeline.tools.repo_index import index_repo, read_files
from forgeline.tools.sandbox import Sandbox
from forgeline.tools.scope_guard import FileWrite, apply_change, check_change
from forgeline.tools.security_scan import Finding, scan_changes
from forgeline.tools.workspace import GitRepo, Worktree

MAX_FEEDBACK_ITEMS = 8


@dataclass
class WorkflowContext:
    """Non-serialisable collaborators shared by all nodes (never stored in the checkpoint)."""

    settings: Settings
    policy: Policy
    db: Database
    router: LLMRouter
    repo: GitRepo
    sandbox: Sandbox
    audit: AuditLog = field(init=False)
    ledger: ActionLedger = field(init=False)
    _worktrees: dict[str, Worktree] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.audit = AuditLog(self.db)
        self.ledger = ActionLedger(self.db)

    @property
    def runs_dir(self) -> Path:
        return self.settings.workspace_dir / "runs"

    def worktree(self, run_id: str) -> Worktree:
        if run_id not in self._worktrees:
            self._worktrees[run_id] = self.repo.create_worktree(run_id, self.runs_dir)
        return self._worktrees[run_id]

    def artifacts(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "artifacts"


def node(name: str) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Wrap a node: lifecycle events, stage tracking, malformed-output escalation."""

    def decorator(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(self: Nodes, state: WorkflowState) -> dict[str, Any]:
            run_id = state["run_id"]
            db = self.ctx.db
            db.update_run(run_id, status="running", stage=name)
            db.add_event(run_id, "node_start", node=name)
            started = time.perf_counter()
            try:
                update = fn(self, state) or {}
            except GraphBubbleUp:
                raise  # interrupts and control flow: not errors
            except (AgentOutputError, StructuredOutputError) as exc:
                db.add_event(run_id, "node_error", node=name, error=str(exc)[:500])
                details = ("; ".join(exc.problems[:5]) if isinstance(exc, AgentOutputError) else exc.last_error)
                update = {"escalation": {
                    "reason": "malformed_output", "node": name,
                    "details": f"The {name} step kept receiving unusable model output: {details}",
                    "resume_node": name, "options": ["retry", "abort"],
                }, "_summary": "agent output rejected after all repairs"}
            except Exception as exc:
                db.add_event(run_id, "node_error", node=name, error=f"{type(exc).__name__}: {str(exc)[:400]}")
                raise
            summary = update.pop("_summary", "")
            db.add_event(run_id, "node_end", node=name, duration_ms=round((time.perf_counter() - started) * 1000),
                         summary=summary)
            return update

        return wrapper

    return decorator


def _requirement(state: WorkflowState) -> Requirement:
    return Requirement(**state["requirement"])


def _analysis(state: WorkflowState) -> AnalysisResult:
    return AnalysisResult.model_validate(state["analysis"])


def _design(state: WorkflowState) -> DesignProposal:
    return DesignProposal.model_validate(state["design"])


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Nodes:
    def __init__(self, ctx: WorkflowContext) -> None:
        self.ctx = ctx
        p = ctx.policy
        mode = ctx.settings.dev_output_mode
        self.analyst = AnalystAgent(ctx.router, p)
        self.architect = ArchitectAgent(ctx.router, p)
        self.qa_agent = QAAgent(ctx.router, p)
        self.developer = DeveloperAgent(ctx.router, p, mode=mode)
        self.security_agent = SecurityAgent(ctx.router, p)
        self.release_agent = ReleaseAgent(ctx.router, p)
        self.evaluator = EvaluatorAgent(ctx.router, p)

    # ------------------------------------------------------------------ helpers
    def _audit(self, state: WorkflowState, actor: str, action: str, details: dict[str, Any] | None = None) -> None:
        self.ctx.audit.append(state["run_id"], actor, action, details or {})

    def _event(self, state: WorkflowState, kind: str, node_name: str, **payload: Any) -> None:
        self.ctx.db.add_event(state["run_id"], kind, node=node_name, **payload)

    def _wt(self, state: WorkflowState) -> Worktree:
        return self.ctx.worktree(state["run_id"])

    def _log_attempt(self, state: WorkflowState, **entry: Any) -> list[dict[str, Any]]:
        log = list(state.get("attempts_log", []))
        log.append({"attempt": state.get("dev_attempt", 0), "ts": utcnow_iso(), **entry})
        return log

    def _budget_escalation(self, state: WorkflowState, what: str) -> dict[str, Any] | None:
        if state.get("dev_attempt", 0) < state.get("dev_budget", self.ctx.policy.autonomy.max_dev_attempts):
            return None
        return {
            "reason": "retry_budget_exhausted", "node": "developer",
            "details": f"{what} still failing after {state.get('dev_attempt')} developer attempt(s). "
                       "Latest problems: " + " | ".join(state.get("feedback", [])[:3]),
            "resume_node": "developer", "options": ["retry", "abort"],
        }

    # ------------------------------------------------------------------ intake
    @node("intake")
    def intake(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        self._audit(state, "system", "run_started", {
            "requirement": state["requirement"]["id"], "provider": state.get("provider"),
            "policy_sha256": self.ctx.policy.sha256, "branch": wt.branch, "base_commit": wt.base_sha,
        })
        return {
            "status": "running", "dev_attempt": 0, "dev_budget": self.ctx.policy.autonomy.max_dev_attempts,
            "feedback": [], "attempts_log": [], "reconcile_rounds": 0, "security_fix_attempts": 0,
            "request_changes_count": 0, "human_decisions": [], "clarifications": [], "objection": "",
            "_summary": f"worktree {wt.branch} @ {wt.base_sha[:8]}",
        }

    # ------------------------------------------------------------------ analyst + clarification gate
    @node("analyst")
    def analyst_node(self, state: WorkflowState) -> dict[str, Any]:
        index = index_repo(Path(self._wt(state).path))
        clarifications = [Clarification(**c) for c in state.get("clarifications", [])]
        analysis = self.analyst.run(
            state["run_id"], requirement=_requirement(state), repo_summary=index.summary(),
            clarifications=clarifications,
        )
        self._audit(state, "agent:analyst", "analysis_produced", {
            "criteria": len(analysis.acceptance_criteria), "questions": analysis.clarifying_questions,
            "ambiguity": analysis.ambiguity_score,
        })
        return {"analysis": analysis.model_dump(),
                "_summary": f"{len(analysis.acceptance_criteria)} criteria, {len(analysis.tasks)} tasks, "
                            f"{len(analysis.clarifying_questions)} question(s)"}

    @node("clarify")
    def clarify(self, state: WorkflowState) -> dict[str, Any]:
        questions = _analysis(state).clarifying_questions
        answer = interrupt({
            "gate": "clarification", "questions": questions,
            "requirement": state["requirement"]["title"],
            "assumptions": _analysis(state).assumptions,
        })
        answers = list(answer.get("answers", []))
        actor = answer.get("actor", "human")
        clarifications = [
            {"question": q, "answer": (answers[i] if i < len(answers) and answers[i] else "Use your best judgement.")}
            for i, q in enumerate(questions)
        ]
        self.ctx.db.record_approval(state["run_id"], "clarification", "answered", actor, json.dumps(answers))
        self._audit(state, f"human:{actor}", "clarification_answered", {"clarifications": clarifications})
        return {
            "clarifications": clarifications,
            "human_decisions": [*state.get("human_decisions", []),
                                {"gate": "clarification", "actor": actor, "ts": utcnow_iso()}],
            "_summary": f"{len(clarifications)} answer(s) from {actor}",
        }

    # ------------------------------------------------------------------ architect + reconciliation
    @node("architect")
    def architect_node(self, state: WorkflowState) -> dict[str, Any]:
        index = index_repo(Path(self._wt(state).path))
        objection = state.get("objection", "")
        previous = _design(state) if objection and state.get("design") else None
        design = self.architect.run(
            state["run_id"], requirement=_requirement(state), analysis=_analysis(state), index=index,
            objection=objection, previous=previous,
        )
        warnings = ArchitectAgent.impact_warnings(design, index)
        self._audit(state, "agent:architect", "design_proposed", {
            "allowlist": sorted(design.allowlist()), "warnings": warnings, "revision": bool(objection),
        })
        return {"design": design.model_dump(), "impact_warnings": warnings, "objection": "",
                "_summary": f"{len(design.impacted_components)} file(s): {', '.join(sorted(design.allowlist()))}"}

    @node("reconcile")
    def reconcile(self, state: WorkflowState) -> dict[str, Any]:
        rounds = state.get("reconcile_rounds", 0) + 1
        self._audit(state, "system", "design_disagreement", {"objection": state.get("objection"), "round": rounds})
        if rounds > self.ctx.policy.autonomy.max_reconcile_rounds:
            return {"reconcile_rounds": rounds, "escalation": {
                "reason": "design_deadlock", "node": "reconcile",
                "details": f"Developer still objects after {rounds - 1} reconciliation round(s): "
                           f"{state.get('objection')}",
                "resume_node": "architect", "options": ["retry", "abort"],
            }, "_summary": "deadlock: escalating to a human"}
        return {"reconcile_rounds": rounds, "_summary": f"round {rounds}: architect revises the design"}

    # ------------------------------------------------------------------ QA: acceptance tests first
    @node("qa_author")
    def qa_author(self, state: WorkflowState) -> dict[str, Any]:
        wt_path = Path(self._wt(state).path)
        req = _requirement(state)
        path = acceptance_test_path(req.id)
        index = index_repo(wt_path)
        examples = read_files(wt_path, ["tests/conftest.py", "tests/test_api.py"])
        feedback = ""
        suite, baseline = None, None
        for _ in range(self.ctx.policy.autonomy.max_repair_attempts + 1):
            suite = self.qa_agent.run(state["run_id"], requirement=req, analysis=_analysis(state),
                                      repo_summary=index.summary(), example_tests=examples, feedback=feedback)
            (wt_path / path).write_text(suite.content, encoding="utf-8", newline="\n")
            baseline = run_pytest(self.ctx.sandbox, wt_path, self.ctx.artifacts(state["run_id"]),
                                  targets=[path], label="acceptance_baseline")
            broken = broken_test_reasons([c.message for c in baseline.failures()])
            if not baseline.collection_error and not broken:
                break
            if baseline.collection_error:
                feedback = f"The test file could not be collected by pytest:\n{baseline.collection_error[-1500:]}"
            else:
                examples_failing = "\n".join(f"- {c.nodeid}: {c.message[:300]}" for c in baseline.failures()[:3])
                feedback = ("Before the feature exists the tests must fail because of the missing behaviour, "
                            f"but they fail because the tests themselves are broken: {'; '.join(broken)}.\n"
                            f"{examples_failing}")
            self._event(state, "qa_tests_rejected", "qa_author", error=feedback[:300])
        assert suite is not None and baseline is not None
        if baseline.collection_error or broken_test_reasons([c.message for c in baseline.failures()]):
            (wt_path / path).unlink(missing_ok=True)
            raise AgentOutputError("qa", ["acceptance tests are broken (they fail for reasons unrelated to the feature)"])
        warning = ""
        if baseline.failed == 0 and baseline.errors == 0:
            warning = "acceptance tests already pass on the unchanged code; they may not test the new behaviour"
        self._audit(state, "agent:qa", "acceptance_tests_written", {
            "path": path, "sha": _sha(suite.content), "tests": baseline.total,
            "failing_before_change": baseline.failed + baseline.errors,
        })
        return {"acceptance": {
            "path": path, "content": suite.content, "covered_criteria": suite.covered_criteria,
            "baseline": {"passed": baseline.passed, "failed": baseline.failed, "errors": baseline.errors},
            "warning": warning,
        }, "_summary": f"{baseline.total} tests; {baseline.failed + baseline.errors} fail before the change (expected)"}

    # ------------------------------------------------------------------ developer
    @node("developer")
    def developer_node(self, state: WorkflowState) -> dict[str, Any]:
        wt_path = Path(self._wt(state).path)
        design = _design(state)
        attempt = state.get("dev_attempt", 0) + 1
        allow = sorted(design.allowlist())
        files = read_files(wt_path, allow)
        context = read_files(wt_path, [p for p in design.context_files if p not in allow])
        change = self.developer.run(
            state["run_id"], requirement=_requirement(state), analysis=_analysis(state), design=design,
            files=files, context=context, acceptance_tests=state.get("acceptance", {}).get("content", ""),
            feedback=state.get("feedback", []), attempt=attempt,
        )
        if change.design_objection.strip():
            self._audit(state, "agent:developer", "design_objection", {"objection": change.design_objection})
            return {"dev_attempt": attempt, "objection": change.design_objection,
                    "last_change": {"objection": change.design_objection},
                    "_summary": "objected to the design"}
        try:
            writes = self.developer.resolve(change, files, repo_root=wt_path)
            last = {"files": [{"path": w.path, "content": w.content} for w in writes], "rationale": change.rationale,
                    "edits": sum(len(f.edits) for f in change.files)}
        except EditError as exc:
            last = {"error": str(exc), "rationale": change.rationale}
        return {"dev_attempt": attempt, "objection": "", "last_change": last,
                "_summary": f"attempt {attempt}: " + (
                    f"{len(last['files'])} file(s), {last['edits']} edit(s)" if "files" in last else "edits did not apply")}

    # ------------------------------------------------------------------ scope guard + write
    @node("apply_change")
    def apply_change_node(self, state: WorkflowState) -> dict[str, Any]:
        wt_path = Path(self._wt(state).path)
        last = state.get("last_change", {})
        allow = _design(state).allowlist()
        if "error" in last:
            feedback = [f"Your edits could not be applied: {last['error']}"]
            update: dict[str, Any] = {"feedback": feedback, "scope": {"ok": False, "violations": [
                {"path": "-", "rule": "edit_failed", "reason": last["error"][:300]}]}}
        else:
            writes = [FileWrite(**f) for f in last.get("files", [])]
            verdict = check_change(writes, allow, self.ctx.policy, wt_path)
            if verdict.ok and writes and len(verdict.unchanged) == len(writes):
                verdict.ok = False
                verdict.violations = []
                update = {"feedback": [("Your change is identical to the current files (a repeated no-op action). "
                                        "Make the change the feedback asks for.")],
                          "scope": {"ok": False, "violations": [{"path": "-", "rule": "no_op",
                                                                 "reason": "identical to current content"}]}}
                self._event(state, "repeated_action_blocked", "apply_change")
            elif not verdict.ok:
                violations = [{"path": v.path, "rule": v.rule, "reason": v.reason} for v in verdict.violations]
                update = {"feedback": [f"SCOPE VIOLATION: {v['path']}: {v['reason']}. You may only change: "
                                       f"{', '.join(sorted(allow))}." for v in violations],
                          "scope": {"ok": False, "violations": violations}}
                self._audit(state, "system:scope_guard", "change_rejected", {"violations": violations})
                self._event(state, "scope_violation", "apply_change", violations=violations)
            else:
                inputs = {"attempt": state.get("dev_attempt"), "files": {w.path: _sha(w.content) for w in writes}}
                fresh, key = self.ctx.ledger.claim(state["run_id"], "write_files", inputs)
                if fresh:
                    written = apply_change(writes, wt_path)
                    self.ctx.ledger.complete(key, written)
                    self._audit(state, "agent:developer", "files_written", {"files": written,
                                                                            "attempt": state.get("dev_attempt")})
                else:
                    self._event(state, "duplicate_action_skipped", "apply_change", action="write_files")
                update = {"scope": {"ok": True, "files": [w.path for w in writes], "violations": []}}
        if not update["scope"]["ok"]:
            update["attempts_log"] = self._log_attempt(state, stage="scope", scope_ok=False,
                                                       violations=update["scope"]["violations"])
            escalation = self._budget_escalation({**state, **update}, "Scope/apply problems")  # type: ignore[arg-type]
            if escalation:
                update["escalation"] = escalation
            update["_summary"] = "change rejected before writing"
        else:
            update["_summary"] = f"wrote {len(update['scope']['files'])} file(s) within approved scope"
        return update

    # ------------------------------------------------------------------ QA run: tests + lint
    @node("qa_run")
    def qa_run(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        wt_path = Path(wt.path)
        acceptance_path = state.get("acceptance", {}).get("path", "")
        report = run_pytest(self.ctx.sandbox, wt_path, self.ctx.artifacts(state["run_id"]),
                            label=f"attempt{state.get('dev_attempt', 0)}")
        dev_files = [f for f in wt.changed_files() if f != acceptance_path]
        lint = run_ruff(self.ctx.sandbox, wt_path, dev_files)
        ok = report.ok and lint.ok
        feedback: list[str] = []
        if not report.ok:
            for case in report.failures()[:6]:
                feedback.append(f"TEST FAILED {case.nodeid}: {case.message[:700]}")
            if report.collection_error:
                feedback.append(f"pytest could not run the tests: {report.collection_error[-900:]}")
            if report.timed_out:
                feedback.append("the test run timed out (possible infinite loop)")
        for issue in lint.issues[:10]:
            feedback.append(f"LINT {issue.file}:{issue.line} {issue.code} {issue.message}")
        tests = report.to_dict()
        tests.pop("raw_tail", None)
        qa = {"ok": ok, "tests": tests, "lint": lint.to_dict(), "dev_files": dev_files}
        self._audit(state, "agent:qa", "checks_run", {
            "attempt": state.get("dev_attempt"), "passed": report.passed, "failed": report.failed,
            "errors": report.errors, "coverage": report.coverage_percent, "lint_issues": len(lint.issues),
        })
        update: dict[str, Any] = {
            "qa": qa, "feedback": feedback[:MAX_FEEDBACK_ITEMS] if not ok else [],
            "attempts_log": self._log_attempt(state, stage="qa", scope_ok=True, passed=report.passed,
                                              failed=report.failed, errors=report.errors,
                                              coverage=report.coverage_percent, lint_issues=len(lint.issues)),
            "_summary": f"{report.passed} passed, {report.failed} failed, {report.errors} errors; "
                        f"coverage {report.coverage_percent}%; {len(lint.issues)} lint issue(s)",
        }
        if not ok:
            escalation = self._budget_escalation({**state, **update}, "Tests or lint")  # type: ignore[arg-type]
            if escalation:
                update["escalation"] = escalation
        return update

    # ------------------------------------------------------------------ security
    @node("security")
    def security_node(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        scan = scan_changes(self.ctx.sandbox, Path(wt.path), wt.changed_lines())
        blocking = scan.blocking(self.ctx.policy.security.block_on)
        triage = None
        try:
            triage = self.security_agent.run(state["run_id"], requirement=_requirement(state), diff=wt.diff(),
                                             findings=scan.findings).model_dump()
        except AgentOutputError as exc:  # triage is advisory; static findings still decide
            self._event(state, "triage_unavailable", "security", error=str(exc)[:300])
        security = {**scan.to_dict(), "blocking": [vars(f) for f in blocking], "triage": triage}
        self._audit(state, "agent:security", "security_review", {
            "findings": len(scan.findings), "introduced": len(scan.introduced()), "blocking": len(blocking),
            "rules": sorted({f.rule for f in blocking}),
        })
        update: dict[str, Any] = {"security": security,
                                  "_summary": f"{len(scan.findings)} finding(s), {len(blocking)} blocking"}
        if blocking and not state.get("security_override"):
            fixes = state.get("security_fix_attempts", 0)
            if fixes < self.ctx.policy.autonomy.security_autofix_attempts:
                update.update({
                    "security_fix_attempts": fixes + 1,
                    "dev_budget": state.get("dev_budget", 0) + 1,  # a security fix gets its own attempt
                    "feedback": [_security_feedback(f) for f in blocking[:MAX_FEEDBACK_ITEMS]],
                    "_summary": f"{len(blocking)} blocking finding(s): sent back to the developer to fix",
                })
            else:
                update["escalation"] = {
                    "reason": "security_block", "node": "security",
                    "details": "Blocking security finding(s) remain after an automatic fix attempt: "
                               + "; ".join(f"[{f.severity}] {f.rule} at {f.file}:{f.line}" for f in blocking),
                    "resume_node": "developer", "options": ["retry", "override", "abort"],
                }
                update["_summary"] = "blocking finding(s) remain: escalating to a human"
        return update

    # ------------------------------------------------------------------ evaluation vs baseline
    @node("evaluate")
    def evaluate(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        wt_path = Path(wt.path)
        req = _requirement(state)
        root = self.ctx.settings.root / "baseline"
        manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8")) if (
            root / "manifest.yaml").exists() else {}
        entry = (manifest or {}).get(req.id)
        golden: dict[str, Any] = {"available": False, "passed": 0, "total": 0, "failures": []}
        reference_files: list[str] = []
        if entry:
            src = root / entry["golden_tests"]
            dst_dir = wt_path / "tests" / "golden"
            try:
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst_dir / src.name)
                g = run_pytest(self.ctx.sandbox, wt_path, self.ctx.artifacts(state["run_id"]),
                               targets=[f"tests/golden/{src.name}"], label="golden")
                golden = {"available": True, "passed": g.passed, "total": g.total,
                          "failures": [c.nodeid.split("::")[-1] for c in g.failures()]}
            finally:
                shutil.rmtree(dst_dir, ignore_errors=True)  # never part of the agent's change
            ref_dir = root / entry["reference"]
            reference_files = sorted(p.relative_to(ref_dir).as_posix() for p in ref_dir.rglob("*.py"))
        agent_files = [f for f in wt.changed_files() if f != state.get("acceptance", {}).get("path")]
        evidence = {
            "all_tests_passed": state.get("qa", {}).get("ok", False),
            "golden_tests": f"{golden['passed']}/{golden['total']} passed" if golden["available"] else "n/a",
            "golden_failures": golden["failures"],
            "blocking_security_findings": len(state.get("security", {}).get("blocking", [])),
            "files_changed_by_agent": agent_files,
            "files_changed_by_human_reference": reference_files,
            "developer_attempts": state.get("dev_attempt"),
        }
        rubric = None
        try:
            rubric = self.evaluator.run(state["run_id"], requirement=req, analysis=_analysis(state),
                                        diff=wt.diff(), evidence=evidence)
        except AgentOutputError as exc:
            self._event(state, "rubric_unavailable", "evaluate", error=str(exc)[:300])
        if golden["available"]:
            scores = combine_scores(rubric, golden["passed"], golden["total"])
        else:  # custom requirement: no hidden human baseline exists, so the rubric is the only measure
            rubric_pct = (round(sum(i.score for i in rubric.items) / (5 * len(rubric.items)) * 100, 1)
                          if rubric and rubric.items else None)
            scores = {"golden_pass_rate": None, "rubric_percent": rubric_pct, "overall": rubric_pct}
        evaluation = {"golden": golden, "rubric": rubric.model_dump() if rubric else None, "scores": scores,
                      "reference_files": reference_files, "agent_files": agent_files}
        self._audit(state, "agent:evaluator", "evaluation", {"golden": f"{golden['passed']}/{golden['total']}",
                                                             "scores": scores})
        return {"evaluation": evaluation,
                "_summary": f"golden {golden['passed']}/{golden['total']}; overall {scores.get('overall')}"}

    # ------------------------------------------------------------------ release + approval gate
    @node("release")
    def release_node(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        req = _requirement(state)
        qa = state.get("qa", {})
        tests = qa.get("tests", {})
        security = state.get("security", {})
        facts = {
            "all_tests_passed": qa.get("ok", False),
            "tests": f"{tests.get('passed', 0)} passed, {tests.get('failed', 0)} failed, {tests.get('errors', 0)} errors",
            "coverage_percent": tests.get("coverage_percent"),
            "lint_issues": len(qa.get("lint", {}).get("issues", [])),
            "blocking_security_findings": 0 if state.get("security_override") else len(security.get("blocking", [])),
            "security_override": bool(state.get("security_override")),
            "scope_verdict": "within the architect-approved files",
            "developer_attempts": state.get("dev_attempt"),
            "golden_tests_hidden_from_agents": state.get("evaluation", {}).get("golden", {}).get("passed"),
        }
        summary = self.release_agent.run(
            state["run_id"], requirement=req, analysis=_analysis(state), design=_design(state), facts=facts,
            test_cases=[{"nodeid": c["nodeid"], "outcome": c["outcome"]} for c in tests.get("cases", [])],
            diff_stat=wt.diff_stat(),
        )
        diff = wt.diff()
        fresh, key = self.ctx.ledger.claim(state["run_id"], "commit", {"diff": _sha(diff)})
        if fresh:
            commit = wt.commit_all(f"{req.id}: {summary.headline}\n\nForgeline run {state['run_id']}") or wt.head()
            self.ctx.ledger.complete(key, commit)
            self._audit(state, "agent:release", "branch_committed", {"branch": wt.branch, "commit": commit})
        else:
            commit = self.ctx.ledger.result(key) or wt.head()
        release = {**summary.model_dump(), "facts": facts, "diff_stat": wt.diff_stat(), "branch": wt.branch,
                   "commit": commit}
        self.ctx.db.update_run(state["run_id"], evidence={
            "release": release, "evaluation": state.get("evaluation"), "security": security, "qa": qa,
            "design": state.get("design"), "analysis": state.get("analysis"),
            "impact_warnings": state.get("impact_warnings", []), "attempts": state.get("attempts_log", []),
            "acceptance": {k: v for k, v in state.get("acceptance", {}).items() if k != "content"},
        }, diff=diff)
        return {"release": release, "commit": commit,
                "_summary": f"recommendation: {summary.recommendation}; commit {commit[:8]}"}

    @node("approval")
    def approval(self, state: WorkflowState) -> dict[str, Any]:
        release = state.get("release", {})
        answer = interrupt({
            "gate": "release_approval", "headline": release.get("headline"),
            "recommendation": release.get("recommendation"), "reason": release.get("recommendation_reason"),
            "branch": release.get("branch"), "commit": release.get("commit"),
            "facts": release.get("facts"), "scores": state.get("evaluation", {}).get("scores"),
            "known_risks": release.get("known_risks", []),
        })
        decision = answer.get("decision")
        actor = answer.get("actor", "human")
        comment = answer.get("comment", "")
        self.ctx.db.record_approval(state["run_id"], "release_approval", decision, actor, comment)
        self._audit(state, f"human:{actor}", "release_decision", {"decision": decision, "comment": comment})
        update: dict[str, Any] = {
            "decision": {"gate": "release_approval", "decision": decision, "actor": actor, "comment": comment},
            "human_decisions": [*state.get("human_decisions", []),
                                {"gate": "release_approval", "decision": decision, "actor": actor,
                                 "comment": comment, "ts": utcnow_iso()}],
            "_summary": f"{decision} by {actor}",
        }
        if decision == "request_changes":
            count = state.get("request_changes_count", 0) + 1
            update.update({
                "request_changes_count": count,
                "feedback": [f"HUMAN REVIEWER requested changes: {comment}"],
                "dev_budget": state.get("dev_attempt", 0) + self.ctx.policy.autonomy.max_dev_attempts,
            })
        return update

    @node("merge_deploy")
    def merge_deploy(self, state: WorkflowState) -> dict[str, Any]:
        wt = self._wt(state)
        decision = state.get("decision", {})
        approver = decision.get("actor", "human")
        commit = state.get("commit", "")
        fresh, key = self.ctx.ledger.claim(state["run_id"], "merge", {"commit": commit})
        if fresh:
            merged = self.ctx.repo.merge(wt.branch, f"Merge {wt.branch}: {state['requirement']['title']} "
                                                    f"(approved by {approver})", approver=approver)
            self.ctx.ledger.complete(key, merged)
            self._audit(state, f"human:{approver}", "merged_to_main", {"branch": wt.branch, "merge_commit": merged})
        else:
            merged = self.ctx.ledger.result(key)
        fresh, key = self.ctx.ledger.claim(state["run_id"], "deploy", {"commit": merged})
        record = {"run_id": state["run_id"], "requirement": state["requirement"]["id"], "commit": merged,
                  "environment": "staging (mock)", "approved_by": approver, "ts": utcnow_iso()}
        if fresh:
            path = self.ctx.settings.workspace_dir / "deployments.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            self.ctx.ledger.complete(key, record)
            self._audit(state, "system", "deployed_mock", record)
        return {"outcome": "merged", "status": "completed", "_summary": f"merged {str(merged)[:8]}; mock deploy recorded"}

    # ------------------------------------------------------------------ escalation gate + close
    @node("escalate")
    def escalate(self, state: WorkflowState) -> dict[str, Any]:
        esc = state.get("escalation") or {}
        answer = interrupt({"gate": "escalation", **esc})
        decision = answer.get("decision", "abort")
        actor = answer.get("actor", "human")
        comment = answer.get("comment", "")
        self.ctx.db.record_approval(state["run_id"], f"escalation:{esc.get('reason')}", decision, actor, comment)
        self._audit(state, f"human:{actor}", "escalation_decision",
                    {"reason": esc.get("reason"), "decision": decision, "comment": comment})
        update: dict[str, Any] = {
            "escalation": None,
            "decision": {"gate": "escalation", "decision": decision, "actor": actor, "comment": comment,
                         "resume_node": esc.get("resume_node", "developer"), "reason": esc.get("reason")},
            "human_decisions": [*state.get("human_decisions", []),
                                {"gate": "escalation", "reason": esc.get("reason"), "decision": decision,
                                 "actor": actor, "comment": comment, "ts": utcnow_iso()}],
            "_summary": f"{decision} by {actor}",
        }
        if decision == "retry":
            guidance = [f"HUMAN GUIDANCE: {comment}"] if comment else []
            if esc.get("resume_node") == "architect":
                update["objection"] = f"{state.get('objection', '')}\nHuman guidance: {comment}".strip()
                update["reconcile_rounds"] = 0
            else:
                update["feedback"] = [*guidance, *state.get("feedback", [])][:MAX_FEEDBACK_ITEMS]
                update["dev_budget"] = state.get("dev_attempt", 0) + self.ctx.policy.autonomy.max_dev_attempts
        elif decision == "override":
            update["security_override"] = {"actor": actor, "justification": comment, "ts": utcnow_iso()}
        return update

    @node("close")
    def close(self, state: WorkflowState) -> dict[str, Any]:
        decision = state.get("decision", {}).get("decision")
        outcome = "rejected" if decision in {"reject", "request_changes"} else "aborted"
        self._audit(state, "system", "run_closed", {"outcome": outcome})
        return {"outcome": outcome, "status": outcome, "_summary": outcome}


def _security_feedback(finding: dict[str, Any] | Finding) -> str:
    f = finding if isinstance(finding, dict) else vars(finding)
    return (f"SECURITY BLOCKER [{f['severity']}] {f['rule']} at {f['file']}:{f['line']}: {f['message']}. "
            f"Offending code: {f.get('evidence', '')}. Fix this without breaking the tests "
            "(for card data use payment.masked_card; never log or return the full card number).")
