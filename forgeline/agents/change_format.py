"""Plain-text change format used by the Developer agent (parsed into a CodeChange).

    RATIONALE: one or two sentences
    FILE: app/refunds.py
    <<<<<<< SEARCH
    exact lines copied from the current file
    =======
    replacement lines
    >>>>>>> REPLACE
    FILE: app/new_module.py
    <<<<<<< CONTENT
    complete file content
    >>>>>>> END
    OBJECTION: only when the design cannot work

Code travels verbatim: no JSON escaping, so quotes, backslashes and braces in code are safe even for
small local models.
"""

from __future__ import annotations

import re

from forgeline.schemas import CodeChange, FileChange, TextEdit

FILE_RE = re.compile(r"^\s*(?:#+\s*)?FILE\s*:\s*`?([^`\s]+)`?\s*$", re.IGNORECASE)
SEARCH_RE = re.compile(r"^\s*<{5,}\s*SEARCH\s*$", re.IGNORECASE)
DIVIDER_RE = re.compile(r"^\s*={5,}\s*$")
REPLACE_RE = re.compile(r"^\s*>{5,}\s*REPLACE\s*$", re.IGNORECASE)
CONTENT_RE = re.compile(r"^\s*<{5,}\s*(CONTENT|NEW|FULL)\s*$", re.IGNORECASE)
END_RE = re.compile(r"^\s*>{5,}\s*(END|CONTENT|NEW|FULL)\s*$", re.IGNORECASE)
LABEL_RE = re.compile(r"^\s*(RATIONALE|OBJECTION|DESIGN_OBJECTION)\s*:\s*(.*)$", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*```")


def _strip_fences(lines: list[str]) -> str:
    """Models sometimes wrap a block's body in ``` fences; drop a leading/trailing fence line."""
    if lines and FENCE_RE.match(lines[0]):
        lines = lines[1:]
    if lines and FENCE_RE.match(lines[-1]):
        lines = lines[:-1]
    return "\n".join(lines)


def parse_change(text: str) -> CodeChange:
    lines = text.replace("\r\n", "\n").split("\n")
    files: dict[str, FileChange] = {}
    current: str | None = None
    rationale: list[str] = []
    objection: list[str] = []
    label: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if m := FILE_RE.match(line):
            current, label = m.group(1), None
            files.setdefault(current, FileChange(path=current))
        elif SEARCH_RE.match(line):
            if current is None:
                raise ValueError("a SEARCH block appeared before any 'FILE: <path>' line")
            find, i = _collect(lines, i + 1, DIVIDER_RE, f"SEARCH block in {current} has no ======= divider")
            replace, i = _collect(lines, i + 1, REPLACE_RE, f"SEARCH block in {current} has no >>>>>>> REPLACE end")
            files[current].edits.append(TextEdit(find=_strip_fences(find), replace=_strip_fences(replace)))
            label = None
        elif CONTENT_RE.match(line):
            if current is None:
                raise ValueError("a CONTENT block appeared before any 'FILE: <path>' line")
            body, i = _collect(lines, i + 1, END_RE, f"CONTENT block in {current} has no >>>>>>> END line")
            files[current].content = _strip_fences(body).rstrip("\n") + "\n"
            label = None
        elif m := LABEL_RE.match(line):
            label = "objection" if "OBJECTION" in m.group(1).upper() else "rationale"
            (objection if label == "objection" else rationale).append(m.group(2))
        elif label and line.strip() and not FENCE_RE.match(line):
            (objection if label == "objection" else rationale).append(line.strip())
        i += 1

    change = CodeChange(
        files=[f for f in files.values() if f.edits or f.content.strip()],
        rationale=" ".join(r for r in rationale if r).strip() or "(no rationale given)",
        design_objection=" ".join(o for o in objection if o).strip(),
    )
    if not change.files and not change.design_objection:
        raise ValueError(
            "no changes found; use 'FILE: <path>' followed by SEARCH/REPLACE or CONTENT blocks, "
            "or an 'OBJECTION:' line"
        )
    return change


def _collect(lines: list[str], start: int, end_re: re.Pattern[str], error: str) -> tuple[list[str], int]:
    body = []
    for j in range(start, len(lines)):
        if end_re.match(lines[j]):
            return body, j
        body.append(lines[j])
    raise ValueError(error)
