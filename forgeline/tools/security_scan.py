"""Security scanning: bandit plus payment-specific rules, diff-aware.

Static tools run first and produce evidence; the Security agent's LLM triage (Phase 4) can explain
findings and add context but can never downgrade a blocking static finding.

Each finding is marked `introduced=True` when it sits on a line the agent added or modified. Only
introduced findings can block a release (policy.security.scan_scope = changed_lines); pre-existing
issues are reported for visibility.

Custom rules (payments domain):
  PAN_IN_LOG        critical  card number / CVV referenced in a logging or print call
  PAN_IN_RESPONSE   critical  raw card number returned in an API response dict
  HARDCODED_SECRET  critical  credential-looking literal assigned in source
  LITERAL_PAN       high      a Luhn-valid card number literal in non-test source
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from forgeline.tools.sandbox import Sandbox

SENSITIVE_NAMES = {"card_number", "pan", "cvv", "cvc", "card_cvv", "full_card_number"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
SECRET_ASSIGN = re.compile(
    r"""(?i)\b(api[_-]?key|secret|password|passwd|token|private[_-]?key)\b\s*[:=]\s*["']([^"'\s]{8,})["']"""
)
KNOWN_KEY_PATTERNS = [re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"AIza[0-9A-Za-z_\-]{30,}")]
PAN_LITERAL = re.compile(r"(?<!\d)(\d{13,19})(?!\d)")
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass
class Finding:
    rule: str
    severity: str  # critical | high | medium | low
    file: str
    line: int
    message: str
    evidence: str = ""
    source: str = "custom"  # custom | bandit
    introduced: bool = False
    end_line: int = 0  # last line of a multi-line statement (0 = same as line)


@dataclass
class SecurityScan:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)

    def introduced(self) -> list[Finding]:
        return [f for f in self.findings if f.introduced]

    def blocking(self, block_on: list[str]) -> list[Finding]:
        levels = {s.lower() for s in block_on}
        return [f for f in self.introduced() if f.severity in levels]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "introduced_count": len(self.introduced())}


def _luhn(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id.lower())
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr.lower())
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            # "card_number" used as a dict key / format field
            names.update(n for n in SENSITIVE_NAMES if re.search(rf"\b{n}\b", sub.value.lower()))
    return names


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        target = ast.unparse(func.value).lower()
        return any(word in target for word in ("log", "logger", "logging", "audit"))
    return False


def scan_source(rel_path: str, source: str) -> list[Finding]:
    """Custom rules over one Python file."""
    findings: list[Finding] = []
    lines = source.splitlines()
    is_test = rel_path.startswith("tests/") or Path(rel_path).name.startswith("test_")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding("SYNTAX_ERROR", "low", rel_path, exc.lineno or 0, "file could not be parsed")]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_log_call(node):
            arg_nodes = [*node.args, *(kw.value for kw in node.keywords)]
            hit = set().union(*(_names_in(a) for a in arg_nodes)) & SENSITIVE_NAMES if arg_nodes else set()
            if hit:
                findings.append(Finding(
                    "PAN_IN_LOG", "critical", rel_path, node.lineno,
                    f"sensitive card data ({', '.join(sorted(hit))}) written to logs (PCI-DSS 3.4 violation)",
                    lines[node.lineno - 1].strip()[:200], end_line=node.end_lineno or node.lineno,
                ))
        if not is_test and isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if (isinstance(key, ast.Constant) and key.value in SENSITIVE_NAMES
                        and value is not None and "card_number" in _names_in(value)):
                    findings.append(Finding(
                        "PAN_IN_RESPONSE", "critical", rel_path, node.lineno,
                        "raw card number placed in a response/payload dict", lines[node.lineno - 1].strip()[:200],
                        end_line=node.end_lineno or node.lineno,
                    ))

    for lineno, text in enumerate(lines, start=1):
        match = SECRET_ASSIGN.search(text)
        if match and not is_test and "os.getenv" not in text and "environ" not in text:
            findings.append(Finding("HARDCODED_SECRET", "critical", rel_path, lineno,
                                    f"hard-coded credential in '{match.group(1)}'", text.strip()[:120]))
        if any(p.search(text) for p in KNOWN_KEY_PATTERNS):
            findings.append(Finding("HARDCODED_SECRET", "critical", rel_path, lineno,
                                    "string matches a cloud API key format", "[redacted]"))
        if not is_test:
            for m in PAN_LITERAL.finditer(text):
                if _luhn(m.group(1)):
                    findings.append(Finding("LITERAL_PAN", "high", rel_path, lineno,
                                            "Luhn-valid card number literal in source", "[redacted]"))
    return findings


def run_bandit(sandbox: Sandbox, repo: Path, files: list[str]) -> tuple[list[Finding], str]:
    py_files = [f for f in files if f.endswith(".py") and (repo / f).is_file()
                and not f.startswith("tests/")]  # assert-in-tests noise is not a security signal
    if not py_files:
        return [], ""
    result = sandbox.run("bandit", ["-f", "json", "-q", *py_files], repo)
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return [], (result.stdout + result.stderr)[-500:]
    findings = []
    for item in data.get("results", []):
        findings.append(Finding(
            rule=f"BANDIT_{item.get('test_id')}",
            severity=str(item.get("issue_severity", "low")).lower(),
            file=Path(item.get("filename", "")).as_posix().removeprefix("./"),
            line=int(item.get("line_number", 0)),
            message=item.get("issue_text", ""),
            evidence=(item.get("code") or "").strip()[:200],
            source="bandit",
        ))
    return findings, ""


def scan_changes(sandbox: Sandbox, repo: Path, changed_lines: dict[str, set[int]]) -> SecurityScan:
    """Scan changed files; mark findings on changed lines as introduced."""
    files = [f for f in changed_lines if f.endswith(".py") and (repo / f).is_file()]
    scan = SecurityScan(files_scanned=files)
    for rel in files:
        scan.findings.extend(scan_source(rel, (repo / rel).read_text(encoding="utf-8", errors="replace")))
    bandit_findings, error = run_bandit(sandbox, repo, files)
    scan.findings.extend(bandit_findings)
    if error:
        scan.tool_errors.append(f"bandit: {error}")
    for finding in scan.findings:
        span = range(finding.line, max(finding.line, finding.end_line) + 1)
        finding.introduced = any(n in changed_lines.get(finding.file, set()) for n in span)
    scan.findings.sort(key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.file, f.line))
    return scan
