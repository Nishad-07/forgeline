"""Git workspace: the target repository and one isolated worktree per run.

Layout under the workspace directory:
    target_repo/            the repository agents work on (branch `main`), copied from sample_repo
    runs/<run_id>/repo/     git worktree on branch `agent/<run_id>`: the ONLY place agents write
    runs/<run_id>/artifacts junit/coverage/scanner reports, outside the repo so they never pollute diffs

Agents never touch `main`. Only `GitRepo.merge`, called from the human approval path, writes to it.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

AGENT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Forgeline Developer Agent",
    "GIT_AUTHOR_EMAIL": "developer-agent@forgeline.local",
    "GIT_COMMITTER_NAME": "Forgeline Developer Agent",
    "GIT_COMMITTER_EMAIL": "developer-agent@forgeline.local",
}
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, *, check: bool = True, identity: dict[str, str] | None = None) -> str:
    env = {**os.environ, **(identity or AGENT_IDENTITY), "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout


def _force_rmtree(path: Path) -> None:
    def onerror(func, p, _exc):  # git marks object files read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=onerror)


@dataclass
class GitRepo:
    path: Path

    def git(self, *args: str, check: bool = True, identity: dict[str, str] | None = None) -> str:
        return git(list(args), self.path, check=check, identity=identity)

    def head(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref).strip()

    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def branch_exists(self, name: str) -> bool:
        return bool(self.git("branch", "--list", name).strip())

    def show(self, ref: str, rel_path: str) -> str | None:
        """File content at `ref`, or None if the file does not exist there."""
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{ref}:{rel_path}"], cwd=self.path, capture_output=True, check=False
        )
        if probe.returncode != 0:
            return None
        return self.git("show", f"{ref}:{rel_path}")

    def tracked_files(self, ref: str = "HEAD") -> list[str]:
        return [f for f in self.git("ls-tree", "-r", "--name-only", ref).splitlines() if f]

    # -------------------------------------------------------------- worktrees
    def create_worktree(self, run_id: str, runs_dir: Path, base: str = "main") -> Worktree:
        """Create (or reuse, on resume) the isolated worktree for a run."""
        if not SAFE_RUN_ID.match(run_id):
            raise ValueError(f"unsafe run id {run_id!r}")
        path = runs_dir / run_id / "repo"
        branch = f"agent/{run_id}"
        if path.exists() and (path / ".git").exists():
            base_sha = self.git("merge-base", base, branch).strip()
            return Worktree(path=path, branch=branch, base_sha=base_sha, main_repo=self)
        path.parent.mkdir(parents=True, exist_ok=True)
        base_sha = self.head(base)
        if self.branch_exists(branch):
            self.git("worktree", "add", str(path), branch)
        else:
            self.git("worktree", "add", "-b", branch, str(path), base)
        git(["config", "core.autocrlf", "false"], path)
        return Worktree(path=path, branch=branch, base_sha=base_sha, main_repo=self)

    def remove_worktree(self, path: Path) -> None:
        self.git("worktree", "remove", "--force", str(path), check=False)
        _force_rmtree(path)
        self.git("worktree", "prune", check=False)

    def merge(self, branch: str, message: str, approver: str) -> str:
        """Merge an agent branch into main. Called ONLY after a recorded human approval."""
        if self.branch() != "main":
            self.git("checkout", "main")
        identity = {
            "GIT_AUTHOR_NAME": "Forgeline Developer Agent",
            "GIT_AUTHOR_EMAIL": "developer-agent@forgeline.local",
            "GIT_COMMITTER_NAME": f"{approver} (human approver)",
            "GIT_COMMITTER_EMAIL": "approver@forgeline.local",
        }
        self.git("merge", "--no-ff", branch, "-m", message, identity=identity)
        return self.head()


@dataclass
class Worktree(GitRepo):
    branch: str = ""
    base_sha: str = ""
    main_repo: GitRepo | None = None

    def _stage(self) -> None:
        self.git("add", "-A")

    def changed_files(self) -> list[str]:
        self._stage()
        return [f for f in self.git("diff", "--cached", "--name-only", self.base_sha).splitlines() if f]

    def diff(self, paths: list[str] | None = None) -> str:
        self._stage()
        return self.git("diff", "--cached", self.base_sha, "--", *(paths or []))

    def diff_stat(self) -> dict[str, int]:
        self._stage()
        out = self.git("diff", "--cached", "--numstat", self.base_sha)
        added = removed = files = 0
        for line in out.splitlines():
            a, r, _ = line.split("\t", 2)
            added += int(a) if a.isdigit() else 0
            removed += int(r) if r.isdigit() else 0
            files += 1
        return {"files": files, "added": added, "removed": removed}

    def changed_lines(self) -> dict[str, set[int]]:
        """Line numbers (in the new version) added or modified relative to the run's base commit."""
        self._stage()
        out = self.git("diff", "--cached", "-U0", "--no-color", self.base_sha)
        result: dict[str, set[int]] = {}
        current: str | None = None
        for line in out.splitlines():
            if line.startswith("+++ "):
                target = line[4:].strip()
                current = None if target == "/dev/null" else target.removeprefix("b/")
                if current:
                    result.setdefault(current, set())
            elif line.startswith("@@") and current:
                match = re.search(r"\+(\d+)(?:,(\d+))?", line)
                if match:
                    start, count = int(match.group(1)), int(match.group(2) or "1")
                    result[current].update(range(start, start + count))
        return result

    def base_content(self, rel_path: str) -> str | None:
        return self.show(self.base_sha, rel_path)

    def commit_all(self, message: str) -> str | None:
        self._stage()
        if not self.git("diff", "--cached", "--name-only").strip():
            return None
        self.git("commit", "-m", message)
        return self.head()

    def reset_to_base(self) -> None:
        self.git("reset", "--hard", self.base_sha)
        self.git("clean", "-fd")


def init_target_repo(source: Path, target: Path, *, reset: bool = False) -> GitRepo:
    """Copy the sample repository into the workspace and make it a git repo on `main`."""
    if reset and target.exists():
        _force_rmtree(target)
    if (target / ".git").exists():
        return GitRepo(target)
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", ".coverage")
    )
    git(["init", "-b", "main"], target)
    git(["config", "core.autocrlf", "false"], target)
    git(["add", "-A"], target)
    git(["commit", "-m", "Initial import of PayLite"], target)
    return GitRepo(target)


def reset_workspace(workspace: Path) -> None:
    """Delete all runs and the target repo (used by `forgeline sample init --reset`)."""
    _force_rmtree(workspace / "runs")
    _force_rmtree(workspace / "target_repo")
