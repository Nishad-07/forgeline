"""Workflow state persisted by the LangGraph checkpointer after every node.

Everything is plain JSON (Pydantic models are stored via model_dump) so checkpoints are portable and
readable by the dashboard.
"""

from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    # identity
    run_id: str
    requirement: dict[str, Any]  # Requirement fields
    provider: str

    # planning
    analysis: dict[str, Any]
    clarifications: list[dict[str, str]]
    design: dict[str, Any]
    impact_warnings: list[str]
    objection: str  # developer's design objection awaiting reconciliation
    reconcile_rounds: int

    # QA (test-first)
    acceptance: dict[str, Any]  # {path, content, covered_criteria, baseline: {...}}

    # implementation loop
    dev_attempt: int
    dev_budget: int
    feedback: list[str]
    last_change: dict[str, Any]  # summary of the latest developer output
    scope: dict[str, Any]  # latest scope-guard verdict
    qa: dict[str, Any]  # latest tests + lint results
    security: dict[str, Any]  # latest scan + triage
    security_fix_attempts: int
    attempts_log: list[dict[str, Any]]

    # review and release
    evaluation: dict[str, Any]
    release: dict[str, Any]
    commit: str
    request_changes_count: int

    # control
    escalation: dict[str, Any]  # {reason, details, resume_node, options}
    decision: dict[str, Any]  # last human decision at a gate
    human_decisions: list[dict[str, Any]]
    security_override: dict[str, Any]
    outcome: str  # merged | rejected | aborted
    status: str
