"""Architect agent: impact analysis and design. Its file list becomes the Developer's allowlist."""

from __future__ import annotations

from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.common import fmt_analysis, fmt_design
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, DesignProposal
from forgeline.tools.repo_index import RepoIndex
from forgeline.tools.scope_guard import normalize


class ArchitectAgent(Agent[DesignProposal]):
    name = "architect"
    tier = "code"
    schema = DesignProposal
    system_prompt = (
        "You are the Architect agent in an autonomous engineering organisation.\n"
        "Responsibility: identify exactly which components a requirement impacts and propose a minimal, "
        "safe design.\n"
        "Rules:\n"
        "- Use only file paths that appear in the repository map (for 'modify') or new paths under existing "
        "directories (for 'create').\n"
        "- Keep the change minimal: list only files that must change. Every other source file belongs in "
        "untouched_components.\n"
        "- Include the unit-test file for the main module you change if its tests must be updated.\n"
        "- Do not list acceptance test files; the QA agent writes those separately.\n"
        "- Never list configuration, CI, golden tests or pyproject.toml.\n"
        "- Reply only with the JSON object."
    )

    def build_prompt(
        self, *, requirement: Requirement, analysis: AnalysisResult, index: RepoIndex,
        objection: str = "", previous: DesignProposal | None = None, **_: Any,
    ) -> str:
        parts = [
            f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body}",
            f"# Analysis\n{fmt_analysis(analysis)}",
            index.summary(),
        ]
        if objection and previous is not None:
            parts.append(
                "# Developer objection to your previous design\n"
                f"Previous design:\n{fmt_design(previous)}\n\nObjection:\n{objection}\n\n"
                "Revise the design. If the objection is valid, add the needed files. If it is not, keep the "
                "file list and explain why in design_notes."
            )
        return "\n\n".join(parts)

    def check(self, result: DesignProposal, *, index: RepoIndex, **_: Any) -> list[str]:
        problems = []
        existing = set(index.modules) | set(index.other_files)
        if not result.impacted_components:
            problems.append("impacted_components is empty; at least one file must change")
        if not any(not c.path.startswith("tests/") for c in result.impacted_components):
            problems.append("at least one non-test source file must be impacted")
        if len(result.impacted_components) > self.policy.scope.max_files_changed:
            problems.append(f"too many impacted files (limit {self.policy.scope.max_files_changed})")
        seen = set()
        for comp in result.impacted_components:
            path = normalize(comp.path)
            if path != comp.path:
                problems.append(f"use the normalised path '{path}' instead of '{comp.path}'")
            if path in seen:
                problems.append(f"{path} listed twice")
            seen.add(path)
            if self.policy.is_protected(path):
                problems.append(f"{path} is protected by policy and cannot be changed")
            if comp.change_type == "modify" and path not in existing:
                problems.append(f"{path} does not exist, so it cannot be 'modify' (use an existing path or 'create')")
            if comp.change_type == "create" and path in existing:
                problems.append(f"{path} already exists; use change_type 'modify'")
            if "acceptance" in path:
                problems.append(f"{path}: acceptance tests are written by the QA agent, remove it")
        for path in result.context_files:
            if normalize(path) not in existing:
                problems.append(f"context file {path} does not exist")
        return problems

    @staticmethod
    def impact_warnings(design: DesignProposal, index: RepoIndex) -> list[str]:
        """Deterministic cross-check: importers of changed modules the design did not mention."""
        mentioned = design.allowlist() | set(design.context_files) | set(design.untouched_components)
        warnings = []
        for comp in design.impacted_components:
            for dependent in index.dependents(comp.path):
                if dependent not in mentioned and not index.modules[dependent].is_test:
                    warnings.append(f"{dependent} imports {comp.path} but is not covered by the design")
        return warnings
