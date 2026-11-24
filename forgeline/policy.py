"""Autonomy policy (policy.yaml): retry caps, protected paths, and which decisions need a human."""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, Field


class AutonomyLimits(BaseModel):
    max_dev_attempts: int = 3
    max_reconcile_rounds: int = 1
    max_repair_attempts: int = 2
    security_autofix_attempts: int = 1
    max_request_changes: int = 2


class SecurityPolicy(BaseModel):
    block_on: list[str] = Field(default_factory=lambda: ["critical", "high"])
    scan_scope: str = "changed_lines"


class ScopePolicy(BaseModel):
    protected_paths: list[str] = Field(default_factory=list)
    max_files_changed: int = 6
    max_file_bytes: int = 60_000


class SandboxPolicy(BaseModel):
    command_timeout_s: int = 120
    allowed_commands: list[str] = Field(default_factory=lambda: ["pytest", "ruff", "bandit"])


class LLMPolicy(BaseModel):
    failover: bool = True
    analyst_ambiguity_threshold: float = 0.5


class Policy(BaseModel):
    version: int = 1
    autonomy: AutonomyLimits = Field(default_factory=AutonomyLimits)
    human_required_for: list[str] = Field(default_factory=list)
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)
    scope: ScopePolicy = Field(default_factory=ScopePolicy)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    llm: LLMPolicy = Field(default_factory=LLMPolicy)
    sha256: str = ""  # hash of the source file, recorded in the audit log

    @classmethod
    def load(cls, path: Path) -> Policy:
        raw = path.read_bytes()
        policy = cls.model_validate(yaml.safe_load(raw) or {})
        policy.sha256 = hashlib.sha256(raw).hexdigest()
        return policy

    def requires_human(self, decision: str) -> bool:
        return decision in self.human_required_for

    def blocks(self, severity: str) -> bool:
        return severity.lower() in {s.lower() for s in self.security.block_on}

    def is_protected(self, rel_path: str) -> bool:
        """True if a repository-relative path matches any protected glob."""
        path = PurePosixPath(rel_path.replace("\\", "/")).as_posix()  # also normalises a leading "./"
        for pattern in self.scope.protected_paths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, "*/" + pattern.lstrip("*/")):
                return True
            # "dir/**" also protects the directory entry itself
            if pattern.endswith("/**") and path == pattern[:-3]:
                return True
        return False
