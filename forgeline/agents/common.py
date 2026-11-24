"""Prompt-formatting helpers shared by agents."""

from __future__ import annotations

import ast

from forgeline.schemas import AcceptanceCriterion, AnalysisResult, Clarification, DesignProposal


def fmt_criteria(criteria: list[AcceptanceCriterion]) -> str:
    return "\n".join(f"- {c.id}: GIVEN {c.given} WHEN {c.when} THEN {c.then}" for c in criteria)


def fmt_analysis(analysis: AnalysisResult) -> str:
    parts = [f"Summary: {analysis.summary}", "Acceptance criteria:", fmt_criteria(analysis.acceptance_criteria)]
    if analysis.assumptions:
        parts += ["Assumptions:", *(f"- {a}" for a in analysis.assumptions)]
    if analysis.out_of_scope:
        parts += ["Out of scope:", *(f"- {o}" for o in analysis.out_of_scope)]
    return "\n".join(parts)


def fmt_clarifications(clarifications: list[Clarification]) -> str:
    return "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in clarifications)


def fmt_design(design: DesignProposal) -> str:
    lines = [f"Summary: {design.summary}", "Files to change (the ONLY files you may change):"]
    lines += [f"- {c.path} [{c.change_type}]: {c.reason}" for c in design.impacted_components]
    if design.context_files:
        lines.append(f"Read-only context files: {', '.join(design.context_files)}")
    lines += [f"Design notes: {design.design_notes}", f"Test strategy: {design.test_strategy}"]
    return "\n".join(lines)


def fmt_files(files: dict[str, str | None], header: str) -> str:
    blocks = [header]
    for path, content in files.items():
        if content is None:
            blocks.append(f"\n### {path}\n(new file - does not exist yet)")
        else:
            blocks.append(f"\n### {path}\n```python\n{content.rstrip()}\n```")
    return "\n".join(blocks)


def python_syntax_error(source: str) -> str | None:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    return None
