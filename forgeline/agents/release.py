"""Release agent: assembles the review summary for the human approver.

Facts (test counts, findings, scope verdict) are computed by tools and passed in; the agent writes the
narrative and maps criteria to evidence. Its recommendation is checked for consistency with the facts:
it can never recommend approval when tests fail or a blocking finding is open.
"""

from __future__ import annotations

from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.common import fmt_criteria
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, DesignProposal, ReleaseSummary


class ReleaseAgent(Agent[ReleaseSummary]):
    name = "release"
    tier = "light"
    schema = ReleaseSummary
    system_prompt = (
        "You are the Release agent in an autonomous engineering organisation.\n"
        "Responsibility: prepare an honest review summary so a human can decide whether to merge and deploy.\n"
        "Rules:\n"
        "- Base every statement on the facts provided. Do not claim anything that the evidence does not show.\n"
        "- For each acceptance criterion, state met / not_met / unverified and name the test(s) that show it.\n"
        "- List known risks plainly, including anything unverified.\n"
        "- Recommend 'approve' only if all tests pass and there are no blocking security findings.\n"
        "- Reply only with the JSON object."
    )

    def build_prompt(
        self, *, requirement: Requirement, analysis: AnalysisResult, design: DesignProposal,
        facts: dict[str, Any], test_cases: list[dict[str, Any]], diff_stat: dict[str, int], **_: Any,
    ) -> str:
        tests = "\n".join(f"- {t['nodeid']}: {t['outcome']}" for t in test_cases[:80])
        fact_lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
        return "\n\n".join([
            f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body[:3000]}",
            f"# Acceptance criteria\n{fmt_criteria(analysis.acceptance_criteria)}",
            f"# Design\n{design.summary}\nRisks noted by architect: {'; '.join(design.risks)}",
            f"# Change size\n{diff_stat}",
            f"# Verified facts\n{fact_lines}",
            f"# Test results\n{tests}",
        ])

    def check(
        self, result: ReleaseSummary, *, analysis: AnalysisResult, facts: dict[str, Any], **_: Any
    ) -> list[str]:
        problems = []
        blocked = (not facts.get("all_tests_passed", False)) or facts.get("blocking_security_findings", 0) > 0
        if blocked and result.recommendation == "approve":
            problems.append(
                "recommendation 'approve' contradicts the facts (failing tests or blocking security findings)"
            )
        missing = {c.id for c in analysis.acceptance_criteria} - {e.criterion_id for e in result.criteria_evidence}
        if missing:
            problems.append(f"criteria_evidence is missing criteria {sorted(missing)}")
        return problems
