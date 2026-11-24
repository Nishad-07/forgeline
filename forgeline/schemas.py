"""Agent contracts: the structured output every agent must return.

Schemas are kept flat and explicit so that small local models (Ollama, 3-7B) can satisfy them under
JSON-schema constrained decoding. Field descriptions are sent to the model as part of the schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- analyst


class AcceptanceCriterion(BaseModel):
    id: str = Field(description="Short id like AC1, AC2")
    given: str = Field(description="Precondition")
    when: str = Field(description="Action")
    then: str = Field(description="Observable, testable outcome")


class Task(BaseModel):
    id: str = Field(description="Short id like T1, T2")
    title: str
    description: str


class AnalysisResult(BaseModel):
    summary: str = Field(description="One or two sentences restating the requirement")
    acceptance_criteria: list[AcceptanceCriterion] = Field(description="3-6 testable Given/When/Then criteria")
    tasks: list[Task] = Field(description="Ordered engineering tasks to deliver the requirement")
    clarifying_questions: list[str] = Field(
        description="Questions a human must answer before implementation; empty if the requirement is clear"
    )
    assumptions: list[str] = Field(description="Assumptions made where the requirement is silent")
    out_of_scope: list[str] = Field(description="Things explicitly not being built")
    ambiguity_score: float = Field(ge=0.0, le=1.0, description="0 = fully clear, 1 = cannot proceed")


# --------------------------------------------------------------------------- architect


class ImpactedComponent(BaseModel):
    path: str = Field(description="Repository-relative file path, e.g. app/refunds.py")
    change_type: Literal["modify", "create"]
    reason: str = Field(description="Why this file must change")


class DesignProposal(BaseModel):
    summary: str = Field(description="The proposed design in 2-4 sentences")
    impacted_components: list[ImpactedComponent] = Field(
        description="Every file that must be modified or created. Nothing outside this list may change."
    )
    context_files: list[str] = Field(
        description="Existing files the developer should read but NOT change (e.g. app/errors.py)"
    )
    untouched_components: list[str] = Field(description="Existing source files confirmed NOT affected")
    design_notes: str = Field(description="Data model, API and error-handling decisions")
    test_strategy: str = Field(description="What must be tested")
    risks: list[str] = Field(description="Technical or business risks of the change")

    def allowlist(self) -> set[str]:
        return {c.path for c in self.impacted_components}


# --------------------------------------------------------------------------- developer


class TextEdit(BaseModel):
    find: str = Field(
        description="An exact snippet copied from the CURRENT file (2-6 consecutive lines, unique in the file)"
    )
    replace: str = Field(description="The text that replaces the snippet (may be longer, may be empty)")


class FileChange(BaseModel):
    path: str = Field(description="Repository-relative path of a file in the approved design")
    edits: list[TextEdit] = Field(
        default_factory=list, description="Find/replace edits for an EXISTING file, applied in order"
    )
    content: str = Field(
        default="", description="Complete content, ONLY for a new file (or a full rewrite when asked)"
    )


class CodeChange(BaseModel):
    files: list[FileChange] = Field(description="Every file you change")
    rationale: str = Field(description="How the change satisfies the acceptance criteria")
    design_objection: str = Field(
        default="",
        description="Leave empty if the design is workable. If the design cannot work, explain why and "
        "which additional file is needed; then 'files' may be empty.",
    )


# --------------------------------------------------------------------------- QA


class AcceptanceTestSuite(BaseModel):
    content: str = Field(description="Complete pytest file content")
    covered_criteria: list[str] = Field(description="Acceptance criterion ids covered by the tests")
    notes: str = Field(default="", description="Anything the reviewer should know about the tests")


# --------------------------------------------------------------------------- security


class FindingAssessment(BaseModel):
    rule: str
    file: str
    line: int
    verdict: Literal["confirmed", "false_positive", "needs_review"]
    explanation: str


class SecurityConcern(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    file: str
    line: int = 0
    description: str


class SecurityTriage(BaseModel):
    assessments: list[FindingAssessment] = Field(description="One entry per static finding provided")
    additional_concerns: list[SecurityConcern] = Field(
        description="Issues in the diff the static tools missed (logic flaws, races, data exposure)"
    )
    summary: str


# --------------------------------------------------------------------------- release


class CriterionEvidence(BaseModel):
    criterion_id: str
    status: Literal["met", "not_met", "unverified"]
    evidence: str = Field(description="Which test or check demonstrates it")


class ReleaseSummary(BaseModel):
    headline: str = Field(description="One line describing the change for the approver")
    change_summary: str
    criteria_evidence: list[CriterionEvidence]
    known_risks: list[str]
    rollback_plan: str
    recommendation: Literal["approve", "request_changes", "reject"]
    recommendation_reason: str


# --------------------------------------------------------------------------- evaluator


class RubricItem(BaseModel):
    criterion: Literal["correctness", "scope_discipline", "code_quality", "test_quality", "security"]
    score: int = Field(ge=1, le=5)
    justification: str


class RubricEvaluation(BaseModel):
    items: list[RubricItem] = Field(description="Exactly one item for each of the five rubric criteria")
    overall_comment: str


# --------------------------------------------------------------------------- human inputs


class Clarification(BaseModel):
    question: str
    answer: str
