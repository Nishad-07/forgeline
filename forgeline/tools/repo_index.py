"""AST-based repository index used for impact analysis.

The Architect agent receives a compact summary (modules, public functions, classes, import graph,
which tests cover which modules). The import graph also gives a deterministic cross-check of the
Architect's impact claims: if it changes `app/refunds.py`, everything that imports it is a dependent.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}


@dataclass
class FunctionInfo:
    name: str
    signature: str
    lineno: int
    doc: str = ""


@dataclass
class ClassInfo:
    name: str
    bases: list[str]
    lineno: int
    fields: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    doc: str = ""


@dataclass
class ModuleInfo:
    path: str  # posix, repo-relative
    module: str  # dotted name
    doc: str
    loc: int
    imports: list[str]  # internal modules this module imports
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    is_test: bool = False


@dataclass
class RepoIndex:
    root: Path
    modules: dict[str, ModuleInfo]  # by path
    other_files: list[str]

    def by_module(self, dotted: str) -> ModuleInfo | None:
        return next((m for m in self.modules.values() if m.module == dotted), None)

    def dependents(self, path: str) -> list[str]:
        """Paths of modules that import the module at `path` (direct importers)."""
        target = self.modules.get(path)
        if target is None:
            return []
        return sorted(p for p, m in self.modules.items() if target.module in m.imports and p != path)

    def tests_for(self, path: str) -> list[str]:
        return [p for p in self.dependents(path) if self.modules[p].is_test]

    def source_modules(self) -> list[ModuleInfo]:
        return [m for m in self.modules.values() if not m.is_test]

    def summary(self, max_chars: int = 12_000) -> str:
        """Compact, LLM-friendly description of the repository."""
        lines = ["# Repository map"]
        for m in sorted(self.modules.values(), key=lambda m: (m.is_test, m.path)):
            kind = "test" if m.is_test else "module"
            lines.append(f"\n## {m.path} ({kind}, {m.loc} lines){' - ' + m.doc if m.doc else ''}")
            if m.imports:
                lines.append(f"imports: {', '.join(m.imports)}")
            if not m.is_test:
                deps = self.dependents(m.path)
                if deps:
                    lines.append(f"imported by: {', '.join(deps)}")
            for c in m.classes:
                extra = f" fields: {', '.join(c.fields)}" if c.fields else ""
                methods = f" methods: {', '.join(c.methods)}" if c.methods else ""
                lines.append(f"class {c.name}({', '.join(c.bases)}){extra}{methods}")
            for f in m.functions:
                doc = f"  # {f.doc}" if f.doc else ""
                lines.append(f"def {f.signature}{doc}" if not m.is_test else f"test {f.name}")
            if m.constants:
                lines.append(f"constants: {', '.join(m.constants)}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 20] + "\n...(truncated)"


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().splitlines()[0][:120] if doc and doc.strip() else ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{node.name}({args}){returns}"


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def index_repo(root: Path) -> RepoIndex:
    root = root.resolve()
    py_files: list[Path] = []
    other: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        (py_files if path.suffix == ".py" else other).append(path if path.suffix == ".py" else rel.as_posix())

    internal = {_module_name(p.relative_to(root)) for p in py_files}
    modules: dict[str, ModuleInfo] = {}
    for path in py_files:
        rel = path.relative_to(root)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            modules[rel.as_posix()] = ModuleInfo(rel.as_posix(), _module_name(rel), "SYNTAX ERROR", 0, [])
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
                imports.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
        info = ModuleInfo(
            path=rel.as_posix(),
            module=_module_name(rel),
            doc=_first_line(ast.get_docstring(tree)),
            loc=len(source.splitlines()),
            imports=sorted(i for i in imports if i in internal),
            is_test=rel.parts[0] == "tests" or rel.name.startswith("test_"),
        )
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.functions.append(
                    FunctionInfo(node.name, _signature(node), node.lineno, _first_line(ast.get_docstring(node)))
                )
            elif isinstance(node, ast.ClassDef):
                cls = ClassInfo(
                    node.name, [ast.unparse(b) for b in node.bases], node.lineno,
                    doc=_first_line(ast.get_docstring(node)),
                )
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        cls.fields.append(item.target.id)
                    elif isinstance(item, ast.Assign):
                        cls.fields.extend(t.id for t in item.targets if isinstance(t, ast.Name))
                    elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cls.methods.append(item.name)
                info.classes.append(cls)
            elif isinstance(node, ast.Assign):
                info.constants.extend(t.id for t in node.targets if isinstance(t, ast.Name) and t.id.isupper())
        modules[info.path] = info
    return RepoIndex(root=root, modules=modules, other_files=other)


def read_files(root: Path, paths: list[str], max_bytes: int = 60_000) -> dict[str, str | None]:
    """Read repo-relative files for prompts. Missing files map to None. Refuses paths outside root."""
    root = root.resolve()
    out: dict[str, str | None] = {}
    for rel in paths:
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"path escapes repository: {rel}")
        if target.is_file():
            data = target.read_bytes()[:max_bytes]
            out[rel] = data.decode("utf-8", errors="replace")
        else:
            out[rel] = None
    return out
