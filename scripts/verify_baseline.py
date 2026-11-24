"""Verify the sample repo and the hidden baseline are consistent.

1. sample_repo: tests pass, ruff and bandit are clean.
2. Golden tests FAIL on the unmodified sample repo (the features are missing).
3. Each reference solution passes the existing tests + its golden tests, and stays lint-clean.

Usage: python scripts/verify_baseline.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_repo"
MANIFEST = ROOT / "baseline" / "manifest.yaml"
BIN = Path(sys.executable).parent


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180, check=False)


def pytest(cwd: Path, *targets: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets], cwd)


def last_line(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else proc.stderr.strip()[-200:]


def copy_sample(dest: Path) -> Path:
    target = dest / "repo"
    shutil.copytree(SAMPLE, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache"))
    return target


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def main() -> int:
    results = []

    proc = pytest(SAMPLE)
    results.append(check("sample_repo tests", proc.returncode == 0, last_line(proc)))
    proc = run([str(BIN / "ruff"), "check", ".", "--no-cache"], SAMPLE)
    results.append(check("sample_repo ruff", proc.returncode == 0, last_line(proc)))
    proc = run([str(BIN / "bandit"), "-q", "-r", "app"], SAMPLE)
    results.append(check("sample_repo bandit", proc.returncode == 0, "no issues" if proc.returncode == 0 else proc.stdout[-300:]))

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for req, entry in manifest.items():
        golden_path = ROOT / "baseline" / entry["golden_tests"]
        golden = golden_path.name
        reference = ROOT / "baseline" / entry["reference"]
        with tempfile.TemporaryDirectory() as tmp:
            repo = copy_sample(Path(tmp))
            (repo / "tests" / "golden").mkdir()
            shutil.copy(golden_path, repo / "tests" / "golden" / golden)
            proc = pytest(repo, f"tests/golden/{golden}")
            results.append(check(f"{req} golden tests fail on base repo (expected)", proc.returncode != 0, last_line(proc)))

            shutil.copytree(reference, repo, dirs_exist_ok=True)
            proc = pytest(repo)
            results.append(check(f"{req} reference: all tests incl. golden", proc.returncode == 0, last_line(proc)))
            proc = run([str(BIN / "ruff"), "check", ".", "--no-cache"], repo)
            results.append(check(f"{req} reference ruff", proc.returncode == 0, last_line(proc)))

    print("\nBaseline verified." if all(results) else "\nBaseline verification FAILED.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
