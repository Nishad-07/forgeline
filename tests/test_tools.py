"""Phase 3 tools, exercised against a real copy of the sample repository and real git worktrees."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from forgeline.config import ROOT
from forgeline.policy import Policy
from forgeline.tools.quality import run_pytest, run_ruff
from forgeline.tools.repo_index import index_repo, read_files
from forgeline.tools.sandbox import Sandbox, SandboxViolation, scrubbed_env
from forgeline.tools.scope_guard import FileWrite, apply_change, check_change
from forgeline.tools.security_scan import scan_changes, scan_source
from forgeline.tools.workspace import init_target_repo

POLICY = Policy.load(ROOT / "policy.yaml")
ALLOW = {"app/refunds.py", "app/models.py", "app/main.py", "tests/test_refunds.py"}


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "workspace"
    repo = init_target_repo(ROOT / "sample_repo", workspace / "target_repo")
    worktree = repo.create_worktree("run-test", workspace / "runs")
    sandbox = Sandbox(workspace / "runs", POLICY.sandbox.allowed_commands, timeout_s=120)
    return {"repo": repo, "wt": worktree, "sandbox": sandbox, "artifacts": workspace / "runs" / "run-test" / "artifacts"}


def edit(wt, rel, old, new):
    path = Path(wt.path) / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ workspace / git
def test_worktree_is_isolated_from_main(ws):
    repo, wt = ws["repo"], ws["wt"]
    assert wt.branch == "agent/run-test"
    assert repo.branch() == "main"
    edit(wt, "app/refunds.py", "Refund the full captured amount", "Refund the captured amount")
    assert wt.changed_files() == ["app/refunds.py"]
    assert "Refund the captured amount" not in (Path(repo.path) / "app/refunds.py").read_text()
    assert "-    \"\"\"Refund the full captured amount" in wt.diff()


def test_worktree_reused_on_resume(ws):
    again = ws["repo"].create_worktree("run-test", Path(ws["wt"].path).parents[1])
    assert again.path == ws["wt"].path and again.base_sha == ws["wt"].base_sha


def test_changed_lines_include_new_files(ws):
    wt = ws["wt"]
    edit(wt, "app/refunds.py", 'logger = logging.getLogger("paylite.refunds")',
         'logger = logging.getLogger("paylite.refunds")\nAUDIT = True')
    (Path(wt.path) / "app" / "audit.py").write_text("X = 1\nY = 2\n", encoding="utf-8")
    lines = wt.changed_lines()
    assert lines["app/audit.py"] == {1, 2}
    assert len(lines["app/refunds.py"]) >= 1


def test_commit_and_human_merge(ws):
    repo, wt = ws["repo"], ws["wt"]
    edit(wt, "app/refunds.py", "Refund the full captured amount", "Refund the captured amount")
    assert wt.commit_all("agent change") is not None
    assert wt.commit_all("nothing new") is None
    sha = repo.merge(wt.branch, "Merge run-test (approved)", approver="Asha")
    assert repo.head() == sha
    assert "Refund the captured amount" in (Path(repo.path) / "app/refunds.py").read_text()
    log = repo.git("log", "-1", "--format=%cn")
    assert "human approver" in log


def test_unsafe_run_id_rejected(ws):
    with pytest.raises(ValueError):
        ws["repo"].create_worktree("../escape", Path(ws["wt"].path).parents[1])


# ------------------------------------------------------------------ repo index
def test_index_maps_modules_and_dependents(ws):
    index = index_repo(Path(ws["wt"].path))
    refunds = index.modules["app/refunds.py"]
    assert {f.name for f in refunds.functions} == {"refund_payment", "list_refunds"}
    assert "app.payments" in refunds.imports
    assert "app/main.py" in index.dependents("app/refunds.py")
    assert "tests/test_refunds.py" in index.tests_for("app/refunds.py")
    assert "app/merchants.py" not in index.dependents("app/refunds.py")
    summary = index.summary()
    assert "def refund_payment(payment_id: str, reason: str='') -> Refund" in summary
    assert "class PaymentStatus(StrEnum)" in summary


def test_read_files_refuses_escape(ws):
    root = Path(ws["wt"].path)
    files = read_files(root, ["app/refunds.py", "app/missing.py"])
    assert files["app/missing.py"] is None and "def refund_payment" in files["app/refunds.py"]
    with pytest.raises(ValueError):
        read_files(root, ["../../secrets.txt"])


# ------------------------------------------------------------------ scope guard
def test_scope_guard_accepts_allowlisted_change(ws):
    root = Path(ws["wt"].path)
    content = (root / "app/refunds.py").read_text() + "\n# partial refunds\n"
    verdict = check_change([FileWrite("app/refunds.py", content)], ALLOW, POLICY, root)
    assert verdict.ok, verdict.summary()
    written = apply_change([FileWrite("app/refunds.py", content)], root)
    assert written[0]["path"] == "app/refunds.py" and len(written[0]["sha256"]) == 64


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        ("app/merchants.py", "out_of_scope"),          # unrelated file
        ("tests/golden/test_x.py", "protected_path"),  # protected by policy
        ("pyproject.toml", "protected_path"),
        ("../outside.py", "path_traversal"),
        ("C:/Windows/evil.py", "absolute_path"),
        ("/etc/passwd", "absolute_path"),
        ("", "empty_path"),
    ],
)
def test_scope_guard_rejects(ws, path, rule):
    verdict = check_change([FileWrite(path, "x = 1\n")], ALLOW, POLICY, Path(ws["wt"].path))
    assert not verdict.ok
    assert verdict.violations[0].rule == rule


def test_scope_guard_limits(ws):
    root = Path(ws["wt"].path)
    many = [FileWrite(f"app/f{i}.py", "x=1") for i in range(POLICY.scope.max_files_changed + 1)]
    assert any(v.rule == "too_many_files" for v in check_change(many, ALLOW, POLICY, root).violations)
    huge = [FileWrite("app/refunds.py", "x" * (POLICY.scope.max_file_bytes + 1))]
    assert check_change(huge, ALLOW, POLICY, root).violations[0].rule == "too_large"
    assert check_change([], ALLOW, POLICY, root).violations[0].rule == "empty_change"


def test_scope_guard_flags_noop_change(ws):
    root = Path(ws["wt"].path)
    same = (root / "app/refunds.py").read_text()
    verdict = check_change([FileWrite("app/refunds.py", same)], ALLOW, POLICY, root)
    assert verdict.ok and verdict.unchanged == ["app/refunds.py"]


# ------------------------------------------------------------------ sandbox
def test_sandbox_refuses_unknown_tool(ws):
    with pytest.raises(SandboxViolation):
        ws["sandbox"].run("powershell", ["-c", "whoami"], Path(ws["wt"].path))


def test_sandbox_refuses_cwd_outside_runs(ws, tmp_path):
    with pytest.raises(SandboxViolation):
        ws["sandbox"].run("pytest", ["--version"], tmp_path)


def test_sandbox_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "nope")
    env = scrubbed_env()
    assert "GOOGLE_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PATH" in {k.upper() for k in env} or os.name != "nt"


def test_sandbox_timeout(ws):
    wt = Path(ws["wt"].path)
    (wt / "tests" / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(30)\n")
    result = ws["sandbox"].run("pytest", ["-q", "-p", "no:cacheprovider", "tests/test_slow.py"], wt, timeout_s=3)
    assert result.timed_out and not result.ok


# ------------------------------------------------------------------ QA runners
def test_pytest_report_on_clean_repo(ws):
    report = run_pytest(ws["sandbox"], Path(ws["wt"].path), ws["artifacts"])
    assert report.ok, report.raw_tail
    assert report.passed == 21 and report.failed == 0
    assert report.coverage_percent and report.coverage_percent > 90
    assert "app/refunds.py" in report.coverage_by_file
    assert ws["wt"].changed_files() == []  # artifacts never land inside the repo


def test_pytest_report_captures_failures(ws):
    wt = ws["wt"]
    edit(wt, "app/refunds.py", "amount_paise=payment.amount_paise", "amount_paise=payment.amount_paise - 1")
    report = run_pytest(ws["sandbox"], Path(wt.path), ws["artifacts"])
    assert not report.ok
    failed = {c.nodeid for c in report.failures()}
    assert any("test_full_refund" in n for n in failed)
    assert "assert" in report.failures()[0].message


def test_pytest_reports_syntax_error_as_error(ws):
    wt = ws["wt"]
    edit(wt, "app/refunds.py", "def list_refunds(", "def list_refunds((")
    report = run_pytest(ws["sandbox"], Path(wt.path), ws["artifacts"])
    assert not report.ok and report.errors >= 1


def test_ruff_reports_issues_in_changed_files(ws):
    wt = ws["wt"]
    edit(wt, "app/refunds.py", "import logging", "import logging\nimport os")
    report = run_ruff(ws["sandbox"], Path(wt.path), ["app/refunds.py"])
    assert not report.ok
    assert any(i.code == "F401" and i.file == "app/refunds.py" for i in report.issues)
    assert run_ruff(ws["sandbox"], Path(wt.path), ["app/merchants.py"]).ok


# ------------------------------------------------------------------ security
def test_pan_in_log_is_critical_and_introduced(ws):
    wt = ws["wt"]
    edit(
        wt, "app/refunds.py",
        "    store.refunds[refund.id] = refund\n",
        "    store.refunds[refund.id] = refund\n"
        "    logger.info(\n        \"refund audit card=%s\",\n        payment.card_number,\n    )\n",
    )
    scan = scan_changes(ws["sandbox"], Path(wt.path), wt.changed_lines())
    blocking = scan.blocking(POLICY.security.block_on)
    assert [f.rule for f in blocking] == ["PAN_IN_LOG"]
    assert blocking[0].severity == "critical" and blocking[0].file == "app/refunds.py"


def test_masked_card_in_log_is_fine():
    source = 'import logging\nlog = logging.getLogger("a")\nlog.info("card=%s", payment.masked_card)\n'
    assert scan_source("app/refunds.py", source) == []


def test_preexisting_findings_do_not_block(ws):
    wt = ws["wt"]
    # A risky pattern already on main is reported but not attributed to the agent.
    risky = Path(ws["repo"].path) / "app" / "legacy.py"
    risky.write_text('import subprocess\n\ndef run(cmd):\n    return subprocess.call(cmd, shell=True)\n')
    ws["repo"].git("add", "-A")
    ws["repo"].git("commit", "-m", "legacy code")
    shutil.rmtree(wt.path, ignore_errors=True)
    ws["repo"].git("worktree", "prune")
    ws["repo"].git("branch", "-D", "agent/run-test")
    wt2 = ws["repo"].create_worktree("run-test", Path(ws["wt"].path).parents[1])
    legacy = Path(wt2.path) / "app" / "legacy.py"
    legacy.write_text(legacy.read_text() + "\n\nVERSION = 2\n", encoding="utf-8")  # agent touches other lines
    scan = scan_changes(ws["sandbox"], Path(wt2.path), wt2.changed_lines())
    bandit = [f for f in scan.findings if f.source == "bandit" and f.severity == "high"]
    assert bandit, "bandit should report shell=True"
    assert all(not f.introduced for f in bandit)
    assert scan.blocking(POLICY.security.block_on) == []


def test_hardcoded_secret_and_literal_pan():
    source = 'API_KEY = "sk_live_1234567890abcdef"\nTEST_CARD = "4111111111111111"\n'
    rules = {f.rule for f in scan_source("app/config.py", source)}
    assert {"HARDCODED_SECRET", "LITERAL_PAN"} <= rules
    assert scan_source("tests/test_x.py", source) == []  # test fixtures may use test card numbers
