from __future__ import annotations

import pytest

from forgeline.config import ROOT
from forgeline.policy import Policy


@pytest.fixture
def policy() -> Policy:
    return Policy.load(ROOT / "policy.yaml")


def test_loads_project_policy(policy):
    assert policy.autonomy.max_dev_attempts == 3
    assert len(policy.sha256) == 64
    assert policy.requires_human("merge")
    assert policy.requires_human("deploy")
    assert not policy.requires_human("run_tests")


@pytest.mark.parametrize(
    "path",
    [".git/config", ".github/workflows/ci.yml", "tests/golden/test_x.py", "pyproject.toml", ".env", "app/.env",
     "./tests/golden/a.py", "tests\\golden\\b.py"],
)
def test_protected_paths(policy, path):
    assert policy.is_protected(path)


@pytest.mark.parametrize("path", ["app/refunds.py", "tests/test_refunds.py", "app/models.py", "README.md"])
def test_unprotected_paths(policy, path):
    assert not policy.is_protected(path)


def test_security_blocking(policy):
    assert policy.blocks("CRITICAL")
    assert policy.blocks("high")
    assert not policy.blocks("medium")


def test_hash_changes_with_content(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("autonomy: {max_dev_attempts: 3}\n")
    b.write_text("autonomy: {max_dev_attempts: 4}\n")
    assert Policy.load(a).sha256 != Policy.load(b).sha256
