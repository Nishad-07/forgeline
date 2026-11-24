"""Requirement documents: Markdown with a small YAML front-matter block."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    body: str
    path: str = ""
    priority: str = ""

    @classmethod
    def from_text(cls, text: str, path: str = "") -> Requirement:
        match = FRONT_MATTER.match(text.replace("\r\n", "\n"))
        meta: dict = {}
        body = text
        if match:
            meta = yaml.safe_load(match.group(1)) or {}
            body = match.group(2)
        title = str(meta.get("title") or next(
            (ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), "Untitled requirement"
        ))
        req_id = str(meta.get("id") or (Path(path).stem.upper() if path else "REQ"))
        return cls(id=req_id, title=title, body=body.strip(), path=path, priority=str(meta.get("priority", "")))

    @classmethod
    def load(cls, path: Path) -> Requirement:
        return cls.from_text(path.read_text(encoding="utf-8"), path=path.as_posix())
