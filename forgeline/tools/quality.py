"""QA tooling: pytest (JUnit + coverage) and ruff, run inside the sandbox, parsed into structured reports."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # parses our own pytest output, not untrusted input
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from forgeline.tools.sandbox import CommandResult, Sandbox


@dataclass
class TestCaseResult:
    nodeid: str
    outcome: str  # passed | failed | error | skipped
    duration_s: float = 0.0
    message: str = ""


@dataclass
class TestReport:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    coverage_percent: float | None = None
    coverage_by_file: dict[str, float] = field(default_factory=dict)
    cases: list[TestCaseResult] = field(default_factory=list)
    timed_out: bool = False
    collection_error: str = ""
    raw_tail: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0 and not self.timed_out and self.total > 0

    def failures(self) -> list[TestCaseResult]:
        return [c for c in self.cases if c.outcome in {"failed", "error"}]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total"] = self.total
        data["ok"] = self.ok
        return data


@dataclass
class LintIssue:
    file: str
    line: int
    code: str
    message: str


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.issues and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _parse_junit(path: Path, report: TestReport) -> None:
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        nodeid = f"{classname.replace('.', '/')}.py::{case.get('name')}" if classname else case.get("name", "")
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            child = case.find(tag)
            if child is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (child.get("message") or "") + "\n" + (child.text or "")
                break
        report.cases.append(TestCaseResult(nodeid, outcome, float(case.get("time", 0) or 0), message.strip()[:3000]))
    report.passed = sum(c.outcome == "passed" for c in report.cases)
    report.failed = sum(c.outcome == "failed" for c in report.cases)
    report.errors = sum(c.outcome == "error" for c in report.cases)
    report.skipped = sum(c.outcome == "skipped" for c in report.cases)
    report.duration_s = round(sum(c.duration_s for c in report.cases), 3)


def run_pytest(
    sandbox: Sandbox, repo: Path, artifacts: Path, *, targets: list[str] | None = None,
    label: str = "pytest", coverage_source: str = "app",
) -> TestReport:
    artifacts.mkdir(parents=True, exist_ok=True)
    junit = artifacts / f"{label}.junit.xml"
    cov_json = artifacts / f"{label}.coverage.json"
    for stale in (junit, cov_json):
        stale.unlink(missing_ok=True)
    args = [
        "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
        f"--cov={coverage_source}", f"--cov-report=json:{cov_json}", "--cov-report=",
        *(targets or []),
    ]
    result = sandbox.run("pytest", args, repo, env={"COVERAGE_FILE": str(artifacts / f".{label}.coverage")})
    report = TestReport(timed_out=result.timed_out, raw_tail=(result.stdout + result.stderr)[-4000:])
    if junit.exists():
        _parse_junit(junit, report)
    if result.returncode in (2, 3, 4) or (not junit.exists() and not result.ok):
        report.collection_error = (result.stdout + result.stderr)[-3000:]
        if report.errors == 0 and report.failed == 0:
            report.errors = 1
    if cov_json.exists():
        data = json.loads(cov_json.read_text(encoding="utf-8"))
        report.coverage_percent = round(data.get("totals", {}).get("percent_covered", 0.0), 1)
        report.coverage_by_file = {
            Path(f).as_posix(): round(v["summary"]["percent_covered"], 1) for f, v in data.get("files", {}).items()
        }
    return report


def run_ruff(sandbox: Sandbox, repo: Path, files: list[str]) -> LintReport:
    py_files = [f for f in files if f.endswith(".py") and (repo / f).is_file()]
    report = LintReport(files_checked=py_files)
    if not py_files:
        return report
    result: CommandResult = sandbox.run("ruff", ["check", "--output-format", "json", "--no-cache", *py_files], repo)
    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        report.error = (result.stdout + result.stderr)[-1000:]
        return report
    for item in items:
        file = Path(item.get("filename", "")).resolve()
        rel = file.relative_to(repo.resolve()).as_posix() if file.is_relative_to(repo.resolve()) else str(file)
        report.issues.append(LintIssue(rel, item.get("location", {}).get("row", 0), item.get("code") or "",
                                       item.get("message", "")))
    return report
