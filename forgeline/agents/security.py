"""Security agent: LLM triage on top of deterministic static findings.

Blocking decisions come from the static scanners and policy only. The LLM explains findings and may
raise extra concerns, but a 'false_positive' verdict on a blocking finding does NOT unblock it; only a
human override with a written justification can (policy: security_override).
"""

from __future__ import annotations

from typing import Any

from forgeline.agents.base import Agent
from forgeline.requirements import Requirement
from forgeline.schemas import SecurityTriage
from forgeline.tools.security_scan import Finding


def fmt_findings(findings: list[Finding]) -> str:
    if not findings:
        return "(no static findings)"
    return "\n".join(
        f"- [{f.severity.upper()}] {f.rule} at {f.file}:{f.line} ({'introduced by this change' if f.introduced else 'pre-existing'})"
        f": {f.message} | {f.evidence}"
        for f in findings
    )


class SecurityAgent(Agent[SecurityTriage]):
    name = "security"
    tier = "light"
    schema = SecurityTriage
    system_prompt = (
        "You are the Security agent in an autonomous engineering organisation for a payments company.\n"
        "Responsibility: review a code change for security and compliance risks (PCI-DSS, data exposure, "
        "authorisation, input validation, race conditions on money movement).\n"
        "Rules:\n"
        "- Assess EVERY static finding provided: confirmed, false_positive or needs_review, with a reason.\n"
        "- Add additional_concerns only for concrete issues visible in the diff, with file and line.\n"
        "- Do not invent issues in code that is not shown.\n"
        "- Reply only with the JSON object."
    )

    def build_prompt(
        self, *, requirement: Requirement, diff: str, findings: list[Finding], **_: Any
    ) -> str:
        return "\n\n".join([
            f"# Change request {requirement.id}: {requirement.title}",
            f"# Static analysis findings (bandit + payment rules)\n{fmt_findings(findings)}",
            f"# Diff\n```diff\n{diff[:14000]}\n```",
        ])

    def check(self, result: SecurityTriage, *, findings: list[Finding], **_: Any) -> list[str]:
        expected = min(len(findings), 20)
        if len(result.assessments) < expected:
            return [f"assess every static finding: {expected} provided, {len(result.assessments)} assessed"]
        return []
