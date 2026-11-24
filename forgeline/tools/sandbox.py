"""Process-level sandbox for running checks against agent-written code.

Agent-generated code is executed (by pytest) and must be treated as untrusted. Without Docker on the
demo machine, isolation is enforced at the process level:

* only allowlisted tools (policy.sandbox.allowed_commands) can run; there is no shell
* the working directory must be inside the runs directory (a run's worktree)
* the environment is rebuilt from an allowlist, so API keys and tokens never reach the child
* every command has a timeout; output is truncated

Production upgrade: run the same commands inside a container with no network (see README).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SAFE_ENV_VARS = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA",
}
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
MAX_OUTPUT_CHARS = 20_000


class SandboxViolation(Exception):
    """A command was refused before it ran."""


@dataclass
class CommandResult:
    tool: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _tool_argv(tool: str) -> list[str]:
    """Resolve a logical tool name to an argv prefix, always from this interpreter's environment."""
    if tool in {"pytest", "bandit"}:
        return [sys.executable, "-m", tool]
    if tool == "ruff":
        candidate = Path(sys.executable).parent / ("ruff.exe" if os.name == "nt" else "ruff")
        return [str(candidate)] if candidate.exists() else [sys.executable, "-m", "ruff"]
    raise SandboxViolation(f"unknown tool {tool!r}")


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items()
        if k.upper() in SAFE_ENV_VARS and not any(m in k.upper() for m in SECRET_MARKERS)
    }
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ".", "PYTHONUTF8": "1", "NO_COLOR": "1"})
    env.update(extra or {})
    return env


class Sandbox:
    def __init__(self, runs_dir: Path, allowed_tools: list[str], timeout_s: int = 120) -> None:
        self.runs_dir = runs_dir.resolve()
        self.allowed_tools = set(allowed_tools)
        self.timeout_s = timeout_s

    def run(
        self, tool: str, args: list[str], cwd: Path, *, env: dict[str, str] | None = None,
        timeout_s: int | None = None,
    ) -> CommandResult:
        if tool not in self.allowed_tools:
            raise SandboxViolation(f"tool {tool!r} is not in the sandbox allowlist {sorted(self.allowed_tools)}")
        cwd = cwd.resolve()
        if not cwd.is_relative_to(self.runs_dir):
            raise SandboxViolation(f"working directory {cwd} is outside the runs directory")
        argv = [*_tool_argv(tool), *args]
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                argv, cwd=cwd, env=scrubbed_env(env), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s or self.timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                tool, args, -1, _text(exc.stdout), _text(exc.stderr) + "\n[sandbox] command timed out",
                (time.perf_counter() - started) * 1000, timed_out=True,
            )
        return CommandResult(
            tool, args, proc.returncode, proc.stdout[-MAX_OUTPUT_CHARS:], proc.stderr[-MAX_OUTPUT_CHARS:],
            (time.perf_counter() - started) * 1000,
        )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return (value.decode("utf-8", "replace") if isinstance(value, bytes) else value)[-MAX_OUTPUT_CHARS:]
