"""QA agent: writes acceptance tests from the acceptance criteria, independently of the Developer.

Tests are derived from the requirement contract and the acceptance criteria, never from the Developer's
code, so they act as an independent check. The tests are generated before the implementation exists
(test-first); the workflow verifies they are collectable and that they fail on the unchanged code.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.common import fmt_analysis, python_syntax_error
from forgeline.requirements import Requirement
from forgeline.schemas import AcceptanceTestSuite, AnalysisResult

ALLOWED_IMPORT_ROOTS = {
    "__future__", "pytest", "fastapi", "app", "tests", "typing", "datetime", "decimal", "json", "re",
    "logging", "dataclasses", "uuid", "itertools", "collections",
}
FORBIDDEN_IMPORTS = {"os", "subprocess", "socket", "shutil", "requests", "urllib", "pathlib", "sys"}


BROKEN_TEST_SIGNALS = (
    ("FixtureFunctionDefinition", ("a pytest fixture was imported and used as an object; request fixtures as "
                                   "test-function parameters instead of importing them")),
    ("fixture '", "the test requests a fixture that does not exist"),
    ("NameError", "the test uses an undefined name"),
    ("SyntaxError", "the test file has a syntax error"),
    ("ImportError while importing", "the test file cannot be imported"),
    ("No module named 'tests", "the test imports from a tests module that does not exist"),
    # NOT a signal: "unexpected keyword argument" / AttributeError on app objects - before the feature exists,
    # calling the new signature (e.g. refund_payment(amount_paise=...)) legitimately fails that way.
)


def fixture_names(conftest_source: str | None) -> set[str]:
    """Names of functions decorated with @pytest.fixture in a conftest file."""
    if not conftest_source:
        return set()
    try:
        tree = ast.parse(conftest_source)
    except SyntaxError:
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and any("fixture" in ast.unparse(d) for d in node.decorator_list):
            names.add(node.name)
    return names


def broken_test_reasons(failure_messages: list[str]) -> list[str]:
    """Failures caused by a broken test (not by the missing feature). Used on the pre-change baseline run:
    acceptance tests must fail on the old code, but for the RIGHT reason."""
    reasons = []
    for message in failure_messages:
        for signal, reason in BROKEN_TEST_SIGNALS:
            if signal in message and reason not in reasons:
                reasons.append(reason)
    return reasons


def acceptance_test_path(requirement_id: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in requirement_id.lower())
    return f"tests/test_acceptance_{slug}.py"


class QAAgent(Agent[AcceptanceTestSuite]):
    name = "qa"
    tier = "code"
    schema = AcceptanceTestSuite
    system_prompt = (
        "You are the QA agent in an autonomous engineering organisation.\n"
        "Responsibility: write an independent pytest acceptance-test file that verifies every acceptance "
        "criterion through the public API described in the requirement.\n"
        "Rules:\n"
        "- The feature is NOT implemented yet. Test the behaviour the requirement specifies, not the current code.\n"
        "- Prefer the HTTP API via fastapi.testclient.TestClient(app) from app.main, exactly as the existing "
        "tests do. Use the existing fixtures shown (the store is reset automatically between tests).\n"
        "- Pytest fixtures (e.g. client, merchant, captured_payment) are requested as test-function "
        "PARAMETERS: `def test_x(client):`. NEVER import fixtures; importing them breaks the tests.\n"
        "- Use the exact field names, status codes and error codes stated in the requirement.\n"
        "- Be concise: one short test per acceptance criterion (at most 8 tests), plus the most important "
        "boundary cases. Use a small helper function for repeated setup instead of repeating code.\n"
        "- Only import from: pytest, fastapi, app, tests.conftest, and the standard library basics. "
        "No network, filesystem or subprocess access.\n\n"
        "Output format (plain text, no JSON):\n"
        "COVERS: <comma-separated acceptance criterion ids you test, e.g. AC1, AC2>\n"
        "```python\n<the complete test file>\n```"
    )
    protocol = "text"

    def parse(self, text: str) -> AcceptanceTestSuite:
        blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.DOTALL)
        if blocks:
            content = max(blocks, key=len)
        elif "def test_" in text and python_syntax_error(text) is None:
            content = text
        else:
            raise ValueError("put the complete test file inside a ```python fenced block")
        covers = re.search(r"COVERS?\s*:\s*(.+)", text)
        criteria = [c.strip(" .`") for c in covers.group(1).split(",") if c.strip(" .`")] if covers else []
        return AcceptanceTestSuite(content=content.strip() + "\n", covered_criteria=criteria)

    def build_prompt(
        self, *, requirement: Requirement, analysis: AnalysisResult, repo_summary: str,
        example_tests: dict[str, str | None], feedback: str = "", **_: Any,
    ) -> str:
        examples = "\n".join(
            f"\n### {path}\n```python\n{content.rstrip()}\n```" for path, content in example_tests.items() if content
        )
        parts = [
            f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body}",
            f"# Analysis\n{fmt_analysis(analysis)}",
            f"# Repository map\n{repo_summary[:6000]}",
            f"# Existing test fixtures and style (reuse them){examples}",
            f"# Output\nThe file will be saved as {acceptance_test_path(requirement.id)}.",
        ]
        if feedback:
            parts.append(f"# Problems with your previous test file\n{feedback}")
        return "\n\n".join(parts)

    def check(
        self, result: AcceptanceTestSuite, *, analysis: AnalysisResult,
        example_tests: dict[str, str | None] | None = None, **_: Any,
    ) -> list[str]:
        problems = []
        fixtures = fixture_names((example_tests or {}).get("tests/conftest.py"))
        error = python_syntax_error(result.content)
        if error:
            return [f"the test file has a syntax error at {error}"]
        tree = ast.parse(result.content)
        tests = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        if len(tests) < 3:
            problems.append(f"only {len(tests)} test functions; write at least one per acceptance criterion")
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for mod in modules:
                root = mod.split(".")[0]
                if root in FORBIDDEN_IMPORTS or root not in ALLOWED_IMPORT_ROOTS:
                    problems.append(f"import of '{mod}' is not allowed in acceptance tests")
                if mod.startswith("tests.golden"):
                    problems.append("acceptance tests must not use golden tests")
            if isinstance(node, ast.ImportFrom):
                imported_fixtures = sorted({a.name for a in node.names} & fixtures)
                if imported_fixtures:
                    problems.append(
                        f"do not import the fixtures {imported_fixtures}; request them as test-function "
                        f"parameters, e.g. def test_x({imported_fixtures[0]}):"
                    )
        known = {c.id for c in analysis.acceptance_criteria}
        unknown = [c for c in result.covered_criteria if c not in known]
        if unknown:
            problems.append(f"covered_criteria contains unknown ids {unknown}; valid ids: {sorted(known)}")
        return problems
