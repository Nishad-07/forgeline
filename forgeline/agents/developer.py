"""Developer agent: implements the scoped change.

Two output modes (Settings.dev_output_mode / FORGELINE_DEV_OUTPUT):
  edits (default) - find/replace edits for existing files, complete content only for new files.
                    3-5x fewer output tokens than full files; essential for local models that
                    generate a few tokens per second.
  full            - complete content of every changed file. Most robust for weak models, slowest.

Edits are resolved into full file contents locally (`resolve`), so everything downstream (scope guard,
tests, diff, audit) works on concrete files. The scope rule itself is enforced outside the agent by
the scope guard before any write, so violations are visible, audited and fed back.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from forgeline.agents.base import Agent
from forgeline.agents.change_format import parse_change
from forgeline.agents.common import fmt_analysis, fmt_design, fmt_files, python_syntax_error
from forgeline.llm.router import LLMRouter
from forgeline.policy import Policy
from forgeline.requirements import Requirement
from forgeline.schemas import AnalysisResult, CodeChange, DesignProposal, TextEdit
from forgeline.tools.scope_guard import FileWrite, normalize

LAZY_MARKERS = re.compile(
    r"(rest of (the )?(file|code)|\.\.\. ?(existing|unchanged|same)|# ?(unchanged|existing code)|"
    r"<\s*(existing|unchanged)[^>]*>|remains the same)",
    re.IGNORECASE,
)

COMMON_RULES = (
    "- Change ONLY the files listed in the design. Do not touch any other file.\n"
    "- Preserve all existing behaviour and keep the existing tests passing unless the requirement "
    "explicitly changes that behaviour.\n"
    "- Follow the existing code style, error classes and conventions. Money is integer paise.\n"
    "- Make the provided acceptance tests pass.\n"
    "- If the design cannot work (e.g. a file that must change is missing from it), do not work around "
    "it: write only an 'OBJECTION:' line explaining exactly what is needed, and no FILE blocks."
)

EDITS_FORMAT = (
    "Output format (plain text, no JSON, no markdown fences around blocks):\n"
    "RATIONALE: <one or two sentences>\n"
    "FILE: <path of an existing file>\n"
    "<<<<<<< SEARCH\n"
    "<2-6 consecutive lines copied EXACTLY from the current file, unique within it>\n"
    "=======\n"
    "<the lines that replace them>\n"
    ">>>>>>> REPLACE\n"
    "(repeat SEARCH/REPLACE blocks as needed; repeat FILE: for each file)\n"
    "For a NEW file:\n"
    "FILE: <path>\n"
    "<<<<<<< CONTENT\n"
    "<complete file content>\n"
    ">>>>>>> END\n"
    "Rules for blocks: keep each SEARCH small and exact (indentation included). To insert code, SEARCH an "
    "anchor line and repeat it in the replacement together with the new lines. Never abbreviate code with "
    "'...' or 'rest unchanged'."
)

FULL_FORMAT = (
    "Output format (plain text, no JSON):\n"
    "RATIONALE: <one or two sentences>\n"
    "FILE: <path>\n"
    "<<<<<<< CONTENT\n"
    "<the COMPLETE new content of the file>\n"
    ">>>>>>> END\n"
    "(repeat for every file you change; never abbreviate, never write 'rest of file unchanged' or '...')"
)


class EditError(ValueError):
    pass


def _fuzzy_span(text: str, find: str) -> tuple[int, int] | None:
    """Locate `find` in `text` ignoring leading/trailing whitespace per line. Unique match only."""
    want = [ln.strip() for ln in find.strip("\n").split("\n")]
    if not any(want):
        return None
    lines = text.split("\n")
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    matches = [
        i for i in range(len(lines) - len(want) + 1)
        if [ln.strip() for ln in lines[i : i + len(want)]] == want
    ]
    if len(matches) != 1:
        return None
    start = offsets[matches[0]]
    last = matches[0] + len(want) - 1
    return start, offsets[last] + len(lines[last])


def apply_edits(original: str, edits: list[TextEdit], path: str = "") -> str:
    """Apply find/replace edits in order. Raises EditError listing every edit that could not apply."""
    text = original.replace("\r\n", "\n")
    problems = []
    for number, edit in enumerate(edits, start=1):
        find = edit.find.replace("\r\n", "\n")
        replace = edit.replace.replace("\r\n", "\n")
        if not find.strip():
            problems.append(f"{path} edit {number}: `find` is empty")
            continue
        count = text.count(find)
        if count == 1:
            text = text.replace(find, replace, 1)
            continue
        if count > 1:
            problems.append(f"{path} edit {number}: `find` matches {count} places; include more surrounding lines")
            continue
        span = _fuzzy_span(text, find)
        if span is None:
            first = next((ln.strip() for ln in find.split("\n") if ln.strip()), "")
            problems.append(
                f"{path} edit {number}: `find` text not found in the current file (starts with {first[:80]!r}); "
                "copy the snippet exactly from the current file content"
            )
            continue
        start, end = span
        text = text[:start] + replace.strip("\n") + text[end:]
    if problems:
        raise EditError("; ".join(problems))
    return text


class DeveloperAgent(Agent[CodeChange]):
    name = "developer"
    tier = "code"
    schema = CodeChange
    protocol = "text"

    def parse(self, text: str) -> CodeChange:
        return parse_change(text)

    def __init__(self, router: LLMRouter, policy: Policy, mode: str = "edits") -> None:
        super().__init__(router, policy)
        if mode not in {"edits", "full"}:
            raise ValueError("mode must be 'edits' or 'full'")
        self.mode = mode
        self.system_prompt = (  # type: ignore[misc]
            "You are the Developer agent in an autonomous engineering organisation.\n"
            "Responsibility: implement the approved design in the existing codebase.\n"
            f"Rules:\n{COMMON_RULES}\n\n{EDITS_FORMAT if mode == 'edits' else FULL_FORMAT}"
        )

    def build_prompt(
        self, *, requirement: Requirement, analysis: AnalysisResult, design: DesignProposal,
        files: dict[str, str | None], context: dict[str, str | None], acceptance_tests: str = "",
        feedback: list[str] | None = None, attempt: int = 1, **_: Any,
    ) -> str:
        parts = [
            f"# Requirement {requirement.id}: {requirement.title}\n\n{requirement.body}",
            f"# Analysis\n{fmt_analysis(analysis)}",
            f"# Approved design\n{fmt_design(design)}",
            fmt_files(files, "# Current content of the files you may change"),
        ]
        if context:
            parts.append(fmt_files(context, "# Read-only context (do NOT change these files)"))
        if acceptance_tests:
            parts.append(f"# Acceptance tests written by QA (must pass)\n```python\n{acceptance_tests.rstrip()}\n```")
        if feedback:
            parts.append(
                f"# Attempt {attempt}: your previous attempt was rejected\n"
                + "\n".join(f"- {item}" for item in feedback)
                + "\nFix these problems. The file contents above are the CURRENT state, including your "
                "previous attempt; base your edits on them."
            )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ resolution
    def resolve(
        self, change: CodeChange, files: dict[str, str | None], repo_root: Path | None = None
    ) -> list[FileWrite]:
        """Turn the agent's output into concrete file writes. Raises EditError on unusable output.

        `files` holds the current content of allowlisted files. Paths outside it are read from
        `repo_root` (if given) so that the scope guard, not this method, rejects out-of-scope writes.
        """
        writes, problems = [], []
        for fc in change.files:
            path = normalize(fc.path)
            if fc.content.strip() and not fc.edits:
                writes.append(FileWrite(path, fc.content))
                continue
            if not fc.edits:
                problems.append(f"{path}: neither `edits` nor `content` was provided")
                continue
            original = files.get(path)
            if original is None and repo_root is not None:
                candidate = (repo_root / path).resolve()
                if candidate.is_relative_to(repo_root.resolve()) and candidate.is_file():
                    original = candidate.read_text(encoding="utf-8")
            if original is None:
                if repo_root is None and path not in files:
                    continue  # not resolvable here; the scope guard will judge this path
                problems.append(f"{path}: file does not exist, so it cannot be edited; provide `content`")
                continue
            try:
                writes.append(FileWrite(path, apply_edits(original, fc.edits, path)))
            except EditError as exc:
                problems.append(str(exc))
        if problems:
            raise EditError("; ".join(problems))
        return writes

    def check(self, result: CodeChange, *, files: dict[str, str | None] | None = None, **_: Any) -> list[str]:
        if result.design_objection.strip():
            return []  # an objection is a legitimate outcome; the workflow routes it to the architect
        if not result.files:
            return ["no files returned; return your changes, or a design_objection"]
        problems = []
        seen = set()
        for fc in result.files:
            if fc.path in seen:
                problems.append(f"{fc.path} returned twice; combine its edits into one entry")
            seen.add(fc.path)
            if self.mode == "full" and fc.edits:
                problems.append(f"{fc.path}: full mode requires a CONTENT block, not SEARCH/REPLACE edits")
            if fc.content.strip() and fc.edits:
                problems.append(f"{fc.path}: use either SEARCH/REPLACE blocks or one CONTENT block, not both")
            text = fc.content + "".join(e.replace for e in fc.edits)
            if LAZY_MARKERS.search(text):
                problems.append(f"{fc.path} looks abbreviated; write out the actual code")
        if problems:
            return problems
        try:
            writes = self.resolve(result, files or {})
        except EditError as exc:
            return [str(exc)]
        for write in writes:
            if not write.content.strip():
                problems.append(f"{write.path} would be empty")
            elif write.path.endswith(".py"):
                error = python_syntax_error(write.content)
                if error:
                    problems.append(f"{write.path} would have a syntax error at {error} after your change")
        return problems
