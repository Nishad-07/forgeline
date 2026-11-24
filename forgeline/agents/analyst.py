"""Analyst agent: requirement -> acceptance criteria, task plan, clarifying questions."""

from __future__ import annotations

from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.common import fmt_clarifications
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, Clarification


class AnalystAgent(Agent[AnalysisResult]):
    name = "analyst"
    tier = "light"
    schema = AnalysisResult
    system_prompt = (
        "You are the Analyst agent in an autonomous engineering organisation.\n"
        "Responsibility: turn a product requirement into precise, testable acceptance criteria and an "
        "ordered task plan for engineers.\n"
        "Rules:\n"
        "- Write 3-6 acceptance criteria in Given/When/Then form, ids AC1, AC2, ... Each must be verifiable by an "
        "automated test. Merge closely related cases into one criterion; keep each line short.\n"
        "- Respect any API contract, field names and error codes stated in the requirement exactly.\n"
        "- Ask clarifying questions ONLY for product decisions that change behaviour and that the "
        "requirement leaves open. Do not ask about things the requirement already states.\n"
        "- If you ask questions, still write criteria for everything that is already clear.\n"
        "- ambiguity_score: 0.0 when nothing important is open; 0.5 or more when an open decision affects "
        "behaviour that will be implemented.\n"
        "- Reply only with the JSON object."
    )

    def build_prompt(
        self, *, requirement: Requirement, repo_summary: str = "",
        clarifications: list[Clarification] | None = None, **_: Any,
    ) -> str:
        parts = [f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body}"]
        if repo_summary:
            parts.append(f"# Existing system (for context)\n{repo_summary[:4000]}")
        if clarifications:
            parts.append(
                "# Answers from the product owner\n"
                "These questions were already answered. Do NOT ask them again; reflect the answers in the "
                "criteria and set clarifying_questions to an empty list.\n" + fmt_clarifications(clarifications)
            )
        return "\n\n".join(parts)

    def check(
        self, result: AnalysisResult, *, clarifications: list[Clarification] | None = None, **_: Any
    ) -> list[str]:
        problems = []
        if len(result.acceptance_criteria) < 2:
            problems.append("provide at least 3 acceptance criteria")
        ids = [c.id for c in result.acceptance_criteria]
        if len(ids) != len(set(ids)):
            problems.append("acceptance criterion ids must be unique")
        if not result.tasks:
            problems.append("provide at least one task")
        if clarifications and result.clarifying_questions:
            problems.append("the questions were answered; clarifying_questions must be an empty list")
        if (not clarifications and not result.clarifying_questions
                and result.ambiguity_score >= self.policy.llm.analyst_ambiguity_threshold):
            problems.append(
                f"ambiguity_score is {result.ambiguity_score} but no clarifying_questions were asked; "
                "either ask the questions that resolve the ambiguity or lower the score"
            )
        return problems
