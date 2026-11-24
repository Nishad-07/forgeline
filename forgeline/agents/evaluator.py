"""Evaluator: compares agent output with the hidden human baseline and a fixed rubric.

Deterministic part (computed by the workflow): golden-test pass rate, diff size and files touched
versus the human reference implementation. LLM part (this agent): a 1-5 score on five rubric
criteria with justifications. The final score combines both (see `combine_scores`).
"""

from __future__ import annotations

from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.common import fmt_criteria
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, RubricEvaluation

RUBRIC = {
    "correctness": "Does the change implement every acceptance criterion and the API contract?",
    "scope_discipline": "Is the change minimal and limited to the impacted components?",
    "code_quality": "Is the code readable, idiomatic and consistent with the codebase?",
    "test_quality": "Do the tests meaningfully verify the behaviour, including edge cases?",
    "security": "Does the change avoid introducing security or compliance risks?",
}


class EvaluatorAgent(Agent[RubricEvaluation]):
    name = "evaluator"
    tier = "light"
    schema = RubricEvaluation
    system_prompt = (
        "You are the Evaluator in an autonomous engineering organisation. You grade a completed change "
        "against a fixed rubric, strictly and consistently, as a senior reviewer would.\n"
        "Score each criterion from 1 (poor) to 5 (excellent) and justify it with specifics from the diff "
        "and the evidence. Golden tests were written by a human and hidden from the agents: failures there "
        "are strong evidence against correctness.\nReply only with the JSON object."
    )

    def build_prompt(
        self, *, requirement: Requirement, analysis: AnalysisResult, diff: str, evidence: dict[str, Any], **_: Any
    ) -> str:
        rubric = "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
        facts = "\n".join(f"- {k}: {v}" for k, v in evidence.items())
        return "\n\n".join([
            f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body[:3000]}",
            f"# Acceptance criteria\n{fmt_criteria(analysis.acceptance_criteria)}",
            f"# Rubric\n{rubric}",
            f"# Evidence\n{facts}",
            f"# Diff\n```diff\n{diff[:12000]}\n```",
        ])

    def check(self, result: RubricEvaluation, **_: Any) -> list[str]:
        names = [i.criterion for i in result.items]
        missing = set(RUBRIC) - set(names)
        problems = []
        if missing:
            problems.append(f"missing rubric criteria: {sorted(missing)}")
        if len(names) != len(set(names)):
            problems.append("each rubric criterion must appear exactly once")
        return problems


def combine_scores(rubric: RubricEvaluation | None, golden_passed: int, golden_total: int) -> dict[str, Any]:
    """Overall score out of 100: 60% golden tests (objective), 40% rubric (judgement)."""
    golden_ratio = golden_passed / golden_total if golden_total else 0.0
    rubric_ratio = (
        sum(i.score for i in rubric.items) / (5 * len(rubric.items)) if rubric and rubric.items else None
    )
    overall = golden_ratio * 60 + (rubric_ratio if rubric_ratio is not None else golden_ratio) * 40
    return {
        "golden_pass_rate": round(golden_ratio * 100, 1),
        "rubric_percent": round(rubric_ratio * 100, 1) if rubric_ratio is not None else None,
        "overall": round(overall, 1),
    }
