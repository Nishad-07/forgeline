"""Scope guard: every file write proposed by the Developer agent is checked BEFORE touching disk.

A change is rejected if any file:
* escapes the repository (absolute path, drive letter, `..`, symlink), or
* matches a protected path from policy.yaml (e.g. tests/golden/**, .git/**, pyproject.toml), or
* is not in the allowlist the Architect's design approved (files_to_modify + files_to_create), or
* exceeds size limits, or the change touches too many files.

Rejected changes are returned to the Developer with the reasons; nothing is written.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from forgeline.policy import Policy

DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass
class FileWrite:
    path: str
    content: str


@dataclass
class Violation:
    path: str
    rule: str
    reason: str


@dataclass
class ScopeVerdict:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)  # proposed content identical to current file
    normalized: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return f"OK: {len(self.normalized)} file(s) within approved scope"
        return "; ".join(f"{v.path}: {v.reason}" for v in self.violations)


def normalize(path: str) -> str:
    return PurePosixPath(path.strip().replace("\\", "/")).as_posix()


def check_change(
    writes: list[FileWrite], allowlist: set[str], policy: Policy, repo_root: Path
) -> ScopeVerdict:
    root = repo_root.resolve()
    allowed = {normalize(p) for p in allowlist}
    verdict = ScopeVerdict(ok=True)

    if not writes:
        verdict.violations.append(Violation("-", "empty_change", "the change contains no files"))
    if len(writes) > policy.scope.max_files_changed:
        verdict.violations.append(Violation(
            "-", "too_many_files", f"{len(writes)} files exceeds limit {policy.scope.max_files_changed}"
        ))

    seen: set[str] = set()
    for write in writes:
        raw = write.path or ""
        rel = normalize(raw)
        if not raw.strip():
            verdict.violations.append(Violation(raw, "empty_path", "file path is empty"))
            continue
        if raw.startswith(("/", "\\")) or DRIVE.match(raw):
            verdict.violations.append(Violation(raw, "absolute_path", "absolute paths are not allowed"))
            continue
        if ".." in PurePosixPath(rel).parts:
            verdict.violations.append(Violation(raw, "path_traversal", "'..' is not allowed in paths"))
            continue
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            verdict.violations.append(Violation(raw, "outside_repo", "resolves outside the repository"))
            continue
        if (root / rel).is_symlink():
            verdict.violations.append(Violation(raw, "symlink", "writing through symlinks is not allowed"))
            continue
        if rel in seen:
            verdict.violations.append(Violation(raw, "duplicate", "file listed more than once"))
            continue
        seen.add(rel)
        if policy.is_protected(rel):
            verdict.violations.append(Violation(rel, "protected_path", "protected by policy; agents may not modify it"))
            continue
        if rel not in allowed:
            verdict.violations.append(Violation(
                rel, "out_of_scope", "not in the files approved by the architect's design"
            ))
            continue
        if len(write.content.encode("utf-8")) > policy.scope.max_file_bytes:
            verdict.violations.append(Violation(rel, "too_large", "file exceeds the size limit"))
            continue
        if target.is_file() and target.read_text(encoding="utf-8", errors="replace") == _lf(write.content):
            verdict.unchanged.append(rel)
        verdict.normalized.append(rel)

    verdict.ok = not verdict.violations
    return verdict


def _lf(text: str) -> str:
    text = text.replace("\r\n", "\n")
    return text if text.endswith("\n") else text + "\n"


def apply_change(writes: list[FileWrite], repo_root: Path) -> list[dict[str, str]]:
    """Write files that already passed `check_change`. Returns path + sha256 for the audit log."""
    root = repo_root.resolve()
    written = []
    for write in writes:
        rel = normalize(write.path)
        target = (root / rel).resolve()
        if not target.is_relative_to(root):  # defence in depth; check_change already refused this
            raise ValueError(f"refusing to write outside repository: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _lf(write.content)
        target.write_bytes(content.encode("utf-8"))
        written.append({"path": rel, "sha256": hashlib.sha256(content.encode()).hexdigest()})
    return written
