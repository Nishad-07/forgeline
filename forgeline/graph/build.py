"""LangGraph wiring: nodes, conditional routing and checkpointing.

    intake -> analyst -(questions?)-> clarify [HUMAN] -> analyst
                     -> architect -> qa_author -> developer
    developer -(objection)-> reconcile -> architect (bounded) | escalate [HUMAN]
    developer -> apply_change (scope guard) -(rejected)-> developer | escalate
    apply_change -> qa_run -(failing)-> developer | escalate
    qa_run -> security -(blocking)-> developer (one auto-fix) | escalate
    security -> evaluate -> release -> approval [HUMAN]
    approval -> merge_deploy | developer (request changes) | close
    escalate [HUMAN] -> retry target | evaluate (security override) | close

Routers are pure functions of state; all budgets are enforced by nodes via `escalation`.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from forgeline.graph.nodes import Nodes, WorkflowContext
from forgeline.graph.state import WorkflowState

NODE_ORDER = [
    "intake", "analyst", "clarify", "architect", "reconcile", "qa_author", "developer", "apply_change",
    "qa_run", "security", "evaluate", "release", "approval", "merge_deploy", "escalate", "close",
]


def route_after_analyst(state: WorkflowState, policy_requires_clarification: bool) -> str:
    if state.get("escalation"):
        return "escalate"
    questions = state.get("analysis", {}).get("clarifying_questions") or []
    if questions and not state.get("clarifications") and policy_requires_clarification:
        return "clarify"
    return "architect"


def route_after_architect(state: WorkflowState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "developer" if state.get("acceptance") else "qa_author"


def route_after_developer(state: WorkflowState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "reconcile" if state.get("objection") else "apply_change"


def route_after_reconcile(state: WorkflowState) -> str:
    return "escalate" if state.get("escalation") else "architect"


def route_after_apply(state: WorkflowState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "qa_run" if state.get("scope", {}).get("ok") else "developer"


def route_after_qa(state: WorkflowState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "security" if state.get("qa", {}).get("ok") else "developer"


def route_after_security(state: WorkflowState) -> str:
    if state.get("escalation"):
        return "escalate"
    blocking = state.get("security", {}).get("blocking") or []
    if blocking and not state.get("security_override"):
        return "developer"
    return "evaluate"


def route_after_approval(state: WorkflowState, max_request_changes: int) -> str:
    decision = state.get("decision", {}).get("decision")
    if decision == "approve":
        return "merge_deploy"
    if decision == "request_changes" and state.get("request_changes_count", 0) <= max_request_changes:
        return "developer"
    return "close"


def route_after_escalate(state: WorkflowState) -> str:
    decision = state.get("decision", {})
    if decision.get("decision") == "retry":
        return decision.get("resume_node") or "developer"
    if decision.get("decision") == "override":
        return "evaluate"
    return "close"


def route_after_qa_author(state: WorkflowState) -> str:
    return "escalate" if state.get("escalation") else "developer"


def build_graph(ctx: WorkflowContext, checkpointer: Any) -> Any:
    nodes = Nodes(ctx)
    policy = ctx.policy
    g = StateGraph(WorkflowState)
    g.add_node("intake", nodes.intake)
    g.add_node("analyst", nodes.analyst_node)
    g.add_node("clarify", nodes.clarify)
    g.add_node("architect", nodes.architect_node)
    g.add_node("reconcile", nodes.reconcile)
    g.add_node("qa_author", nodes.qa_author)
    g.add_node("developer", nodes.developer_node)
    g.add_node("apply_change", nodes.apply_change_node)
    g.add_node("qa_run", nodes.qa_run)
    g.add_node("security", nodes.security_node)
    g.add_node("evaluate", nodes.evaluate)
    g.add_node("release", nodes.release_node)
    g.add_node("approval", nodes.approval)
    g.add_node("merge_deploy", nodes.merge_deploy)
    g.add_node("escalate", nodes.escalate)
    g.add_node("close", nodes.close)

    g.add_edge(START, "intake")
    g.add_edge("intake", "analyst")
    g.add_conditional_edges(
        "analyst", lambda s: route_after_analyst(s, policy.requires_human("clarification")),
        ["clarify", "architect", "escalate"],
    )
    g.add_edge("clarify", "analyst")
    g.add_conditional_edges("architect", route_after_architect, ["qa_author", "developer", "escalate"])
    g.add_conditional_edges("qa_author", route_after_qa_author, ["developer", "escalate"])
    g.add_conditional_edges("developer", route_after_developer, ["reconcile", "apply_change", "escalate"])
    g.add_conditional_edges("reconcile", route_after_reconcile, ["architect", "escalate"])
    g.add_conditional_edges("apply_change", route_after_apply, ["qa_run", "developer", "escalate"])
    g.add_conditional_edges("qa_run", route_after_qa, ["security", "developer", "escalate"])
    g.add_conditional_edges("security", route_after_security, ["evaluate", "developer", "escalate"])
    g.add_edge("evaluate", "release")
    g.add_edge("release", "approval")
    g.add_conditional_edges(
        "approval", lambda s: route_after_approval(s, policy.autonomy.max_request_changes),
        ["merge_deploy", "developer", "close"],
    )
    g.add_edge("merge_deploy", END)
    g.add_conditional_edges(
        "escalate", route_after_escalate,
        ["analyst", "architect", "qa_author", "developer", "evaluate", "release", "close"],
    )
    g.add_edge("close", END)
    return g.compile(checkpointer=checkpointer)
