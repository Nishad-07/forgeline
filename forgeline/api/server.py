"""FastAPI server for the Forgeline dashboard. The contract lives in docs/API.md.

Long-running work (Orchestrator.drive / resume) runs in a small thread pool so the POST endpoints return
202 immediately; the dashboard follows progress through the events table (polling or Server-Sent Events).

Run with `uvicorn.run("forgeline.api.server:create_app", factory=True)` (or `forgeline serve`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from forgeline import __version__
from forgeline.config import ROOT, Settings, get_settings
from forgeline.graph.build import NODE_ORDER
from forgeline.orchestrator import TERMINAL, GateError, Orchestrator
from forgeline.requirements import Requirement
from forgeline.store import AuditLog

log = logging.getLogger("forgeline.api")

CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
DEFAULT_DASHBOARD_DIR = ROOT / "dashboard" / "dist"
POLL_INTERVAL_S = 0.5
PING_INTERVAL_S = 15
EVENT_PAGE = 1000
WORKERS = 4

# ---------------------------------------------------------------------------- static workflow topology
NODE_META: dict[str, tuple[str, str]] = {
    "intake": ("Intake", "system"),
    "analyst": ("Analyst", "agent"),
    "clarify": ("Clarification", "human"),
    "architect": ("Architect", "agent"),
    "reconcile": ("Reconcile", "system"),
    "qa_author": ("QA author", "agent"),
    "developer": ("Developer", "agent"),
    "apply_change": ("Scope guard", "guard"),
    "qa_run": ("Tests + lint", "agent"),
    "security": ("Security", "agent"),
    "evaluate": ("Evaluate", "agent"),
    "release": ("Release", "agent"),
    "approval": ("Human approval", "human"),
    "merge_deploy": ("Merge & deploy", "system"),
    "escalate": ("Escalation", "human"),
    "close": ("Close", "system"),
}

# Mirrors forgeline/graph/build.py (START -> intake and the END edges are implicit).
EDGES: list[tuple[str, str, str | None]] = [
    ("intake", "analyst", None),
    ("analyst", "clarify", "questions"),
    ("analyst", "architect", None),
    ("analyst", "escalate", "escalate"),
    ("clarify", "analyst", "answers"),
    ("architect", "qa_author", None),
    ("architect", "developer", "tests exist"),
    ("architect", "escalate", "escalate"),
    ("qa_author", "developer", None),
    ("qa_author", "escalate", "escalate"),
    ("developer", "apply_change", None),
    ("developer", "reconcile", "objection"),
    ("developer", "escalate", "escalate"),
    ("reconcile", "architect", "redesign"),
    ("reconcile", "escalate", "deadlock"),
    ("apply_change", "qa_run", None),
    ("apply_change", "developer", "rejected"),
    ("apply_change", "escalate", "escalate"),
    ("qa_run", "security", None),
    ("qa_run", "developer", "failing"),
    ("qa_run", "escalate", "escalate"),
    ("security", "evaluate", None),
    ("security", "developer", "blocking"),
    ("security", "escalate", "escalate"),
    ("evaluate", "release", None),
    ("release", "approval", None),
    ("approval", "merge_deploy", "approve"),
    ("approval", "developer", "request changes"),
    ("approval", "close", "reject"),
    ("escalate", "analyst", "retry"),
    ("escalate", "architect", "retry"),
    ("escalate", "qa_author", "retry"),
    ("escalate", "developer", "retry"),
    ("escalate", "release", "retry"),
    ("escalate", "evaluate", "override"),
    ("escalate", "close", "abort"),
]


def graph_topology() -> dict[str, Any]:
    nodes = [{"id": n, "label": NODE_META[n][0], "kind": NODE_META[n][1]} for n in NODE_ORDER]
    edges = [{"source": s, "target": t, **({"label": label} if label else {})} for s, t, label in EDGES]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------- request bodies
class StartRunBody(BaseModel):
    requirement_path: str | None = None
    requirement_text: str | None = Field(default=None, max_length=20_000)
    title: str | None = Field(default=None, max_length=200)
    provider: Literal["gemini", "ollama"] | None = None
    cache: bool | None = None


class CancelRunBody(BaseModel):
    actor: str | None = Field(default=None, max_length=80)


class ResumeRunBody(BaseModel):
    decision: dict[str, Any] | None = None
    provider: Literal["gemini", "ollama"] | None = None


# ---------------------------------------------------------------------------- helpers
def _plain(obj: Any) -> Any:
    """Force JSON-compatible data (checkpoint values may hold non-JSON leaf types)."""
    return json.loads(json.dumps(obj, default=str))


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


STALL_AFTER_S = 120


def is_stalled(run: dict[str, Any], active: bool) -> bool:
    """A created/running run that nothing executes any more and that has been silent for a while."""
    if run["status"] not in {"created", "running"} or active:
        return False
    try:
        updated = datetime.fromisoformat(run["updated_at"])
    except ValueError:
        return False
    return (datetime.now(UTC) - updated).total_seconds() > STALL_AFTER_S


def summary_from_row(run: dict[str, Any], active: bool = False) -> dict[str, Any]:
    """RunSummary from a runs row (same fields as Orchestrator.status plus timestamps)."""
    data = run["data"]
    return {"stalled": is_stalled(run, active),
        "id": run["id"], "status": run["status"], "stage": data.get("stage"), "provider": run["provider"],
        "requirement_id": run["requirement_id"], "title": run["title"],
        "gate": data.get("gate") if run["status"] == "awaiting_human" else None,
        "error": data.get("error"), "outcome": data.get("outcome"),
        "created_at": run["created_at"], "updated_at": run["updated_at"],
    }


def diff_stat(diff: str) -> dict[str, int]:
    """files / added / removed counted from unified diff text (hunk lines only)."""
    files = added = removed = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files += 1
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return {"files": files, "added": added, "removed": removed}


def live_worktree_diff(path: Path, base_sha: str) -> str:
    """Diff of a run's worktree against its base, using a throw-away index so a concurrently running
    workflow (which stages into the real index) is never disturbed."""
    with tempfile.TemporaryDirectory(prefix="forgeline-diff-") as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_TERMINAL_PROMPT": "0"}

        def git(*args: str) -> str:
            proc = subprocess.run(["git", *args], cwd=path, env=env, capture_output=True, text=True,
                                  encoding="utf-8", check=True)
            return proc.stdout

        git("read-tree", "HEAD")
        git("add", "-A")
        return git("diff", "--cached", base_sha)


async def _ollama_tags(base_url: str) -> list[str]:
    """Model names installed in the local Ollama server (raises when it is unreachable)."""
    async with httpx.AsyncClient(timeout=2.0) as client:
        resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
        resp.raise_for_status()
        return [str(m.get("name", "")) for m in resp.json().get("models", [])]


def _norm_model(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


class SPAStaticFiles(StaticFiles):
    """Static dashboard with client-side routing: unknown non-API paths fall back to index.html."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]  # normpath on Windows
            if exc.status_code != 404 or (parts and (parts[0] == "api" or "." in parts[-1])):
                raise
            return await super().get_response("index.html", scope)


# ---------------------------------------------------------------------------- runtime
class Runtime:
    """Holds the (lazily built) orchestrator, the worker pool and the runs executing in this process."""

    def __init__(self, orchestrator: Orchestrator | None) -> None:
        self._orch = orchestrator
        self._orch_lock = threading.Lock()
        self.lock = threading.Lock()  # serialises check -> validate -> submit for runs
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="forgeline-run")
        self.active: dict[str, Future[Any]] = {}

    @property
    def orch(self) -> Orchestrator:
        if self._orch is None:
            with self._orch_lock:
                if self._orch is None:
                    self._orch = Orchestrator()
        return self._orch

    @property
    def settings(self) -> Settings:
        return self._orch.settings if self._orch is not None else get_settings()

    def run(self, run_id: str) -> dict[str, Any]:
        run = self.orch.db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        return run

    def summary(self, run_id: str) -> dict[str, Any]:
        return summary_from_row(self.run(run_id), self.is_active(run_id))

    def is_active(self, run_id: str) -> bool:
        future = self.active.get(run_id)
        return future is not None and not future.done()

    def submit(self, run_id: str, fn: Callable[..., Any], *args: Any) -> None:
        """Run `fn(*args)` in the background. Caller holds `self.lock`."""
        self.active[run_id] = self.pool.submit(self._guard, run_id, fn, *args)

    def _guard(self, run_id: str, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:  # drive() records graph errors itself; this catches the rest
            log.exception("background work for run %s failed", run_id)
            try:
                error = f"{type(exc).__name__}: {str(exc)[:500]}"
                self.orch.db.update_run(run_id, status="failed", error=error)
                self.orch.db.add_event(run_id, "run_failed", node=None, error=error)
            except Exception:
                log.exception("could not record the failure of run %s", run_id)
            return None

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------- app factory
def create_app(orchestrator: Orchestrator | None = None, *, dashboard_dir: Path | None = None) -> FastAPI:
    rt = Runtime(orchestrator)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        rt.shutdown()

    app = FastAPI(title="Forgeline API", version=__version__, lifespan=lifespan)
    app.state.runtime = rt
    app.add_middleware(
        CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True, allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ static information
    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/providers")
    async def providers() -> dict[str, Any]:
        settings = rt.settings
        gemini = {
            "name": "gemini", "available": settings.gemini_key_present,
            "detail": "key present" if settings.gemini_key_present else "GOOGLE_API_KEY not set",
            "models": asdict(settings.gemini),
        }
        wanted = asdict(settings.ollama)
        try:
            installed = {_norm_model(n) for n in await _ollama_tags(settings.ollama_base_url)}
        except (httpx.HTTPError, OSError, ValueError) as exc:
            ollama_ok = False
            detail = f"not reachable at {settings.ollama_base_url} ({type(exc).__name__})"
        else:
            ollama_ok = True
            have = sorted({m for m in wanted.values() if _norm_model(m) in installed})
            missing = sorted({m for m in wanted.values() if _norm_model(m) not in installed})
            detail = "reachable"
            if have:
                detail += f"; installed: {', '.join(have)}"
            if missing:
                detail += f"; missing: {', '.join(missing)}"
        ollama = {"name": "ollama", "available": ollama_ok, "detail": detail, "models": wanted}
        return {"default": settings.default_provider, "failover": settings.failover,
                "providers": [gemini, ollama]}

    @app.get("/api/policy")
    def policy() -> dict[str, Any]:
        path = rt.settings.policy_path
        raw = path.read_bytes()
        return {"sha256": hashlib.sha256(raw).hexdigest(), "policy": _plain(yaml.safe_load(raw) or {})}

    @app.get("/api/requirements")
    def requirements() -> list[dict[str, Any]]:
        root = rt.settings.root
        out = []
        for path in sorted((root / "requirements").glob("*.md")):
            req = Requirement.load(path)
            out.append({"path": path.relative_to(root).as_posix(), "id": req.id, "title": req.title,
                        "priority": req.priority, "body": req.body})
        return out

    @app.get("/api/graph")
    def graph() -> dict[str, Any]:
        return graph_topology()

    # ------------------------------------------------------------------ runs
    @app.get("/api/runs")
    def list_runs(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [summary_from_row(r, rt.is_active(r["id"])) for r in rt.orch.db.list_runs(limit=limit)]

    @app.post("/api/runs", status_code=202)
    def start_run(body: StartRunBody) -> dict[str, Any]:
        orch = rt.orch
        if (body.requirement_path is None) == (body.requirement_text is None):
            raise HTTPException(status_code=400, detail="provide exactly one of requirement_text or requirement_path")
        try:
            if body.requirement_text is not None:
                run_id = orch.create(requirement_text=body.requirement_text, title=body.title,
                                     provider=body.provider, cache=body.cache)
            else:
                root = orch.settings.root.resolve()
                path = Path(body.requirement_path)  # type: ignore[arg-type]
                path = (path if path.is_absolute() else root / path).resolve()
                if not path.is_relative_to(root) or path.suffix.lower() != ".md":
                    raise HTTPException(status_code=400,
                                        detail="requirement_path must be a .md file inside the project")
                if not path.is_file():
                    raise HTTPException(status_code=404, detail=f"requirement not found: {body.requirement_path}")
                run_id = orch.create(path.relative_to(root).as_posix(), provider=body.provider, cache=body.cache)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with rt.lock:
            rt.submit(run_id, orch.drive, run_id)
        return rt.summary(run_id)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        run = rt.run(run_id)
        state = rt.orch.state(run_id)
        values = {k: v for k, v in (state.get("values") or {}).items() if k != "diff"}
        return {**summary_from_row(run, rt.is_active(run_id)), "requirement": run["data"].get("requirement"),
                "state": _plain(values), "next": list(state.get("next") or [])}

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, body: ResumeRunBody) -> dict[str, Any]:
        orch = rt.orch
        rt.run(run_id)
        with rt.lock:
            if rt.is_active(run_id):
                raise HTTPException(status_code=409, detail=f"run {run_id} is already executing")
            try:
                command = orch.prepare_resume(run_id, body.decision, provider=body.provider)
            except GateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            rt.submit(run_id, orch.drive, run_id, command)
        return rt.summary(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, body: CancelRunBody | None = None) -> dict[str, Any]:
        rt.run(run_id)
        try:
            rt.orch.cancel(run_id, actor=(body.actor if body else None) or "human")
        except GateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return rt.summary(run_id)

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, after: int = Query(0, ge=0),
                   limit: int = Query(EVENT_PAGE, ge=1, le=10_000)) -> list[dict[str, Any]]:
        rt.run(run_id)
        return rt.orch.db.events(run_id, after_id=after, limit=limit)

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(request: Request, run_id: str, after: int | None = Query(None, ge=0),
                         once: bool = False) -> EventSourceResponse:
        await asyncio.to_thread(rt.run, run_id)
        start = after
        if start is None:
            header = request.headers.get("last-event-id", "")
            start = int(header) if header.isdigit() else 0
        db = rt.orch.db

        async def generate() -> AsyncIterator[dict[str, Any]]:
            last_id = start
            last_key: tuple[Any, ...] | None = None
            while True:
                if not once and await request.is_disconnected():
                    return
                while True:
                    batch = await asyncio.to_thread(db.events, run_id, last_id, EVENT_PAGE)
                    for event in batch:
                        last_id = event["id"]
                        yield {"event": event["kind"], "id": str(event["id"]), "data": _dumps(event)}
                    if len(batch) < EVENT_PAGE:
                        break
                row = await asyncio.to_thread(db.get_run, run_id)
                if row is not None:
                    summary = summary_from_row(row, rt.is_active(run_id))
                    gate = summary["gate"] or {}
                    key = (summary["status"], summary["stage"], gate.get("gate"), summary["provider"])
                    if key != last_key:
                        last_key = key
                        yield {"event": "status", "data": _dumps(summary)}
                if once:
                    return
                await asyncio.sleep(POLL_INTERVAL_S)

        return EventSourceResponse(generate(), ping=PING_INTERVAL_S)

    @app.get("/api/runs/{run_id}/diff")
    def run_diff(run_id: str) -> dict[str, Any]:
        run = rt.run(run_id)
        orch = rt.orch
        stored = run["data"].get("diff")
        gate = (run["data"].get("gate") or {}).get("gate") if run["status"] == "awaiting_human" else None
        worktree_path = orch.settings.workspace_dir / "runs" / run_id / "repo"
        if stored is not None and (run["status"] in TERMINAL or gate == "release_approval"):
            diff = stored
        elif (worktree_path / ".git").exists():
            wt = orch.repo.create_worktree(run_id, orch.settings.workspace_dir / "runs")
            diff = live_worktree_diff(wt.path, wt.base_sha)
        else:
            diff = stored or ""
        return {"diff": diff, "stat": diff_stat(diff)}

    @app.get("/api/runs/{run_id}/usage")
    def run_usage(run_id: str) -> dict[str, Any]:
        rt.run(run_id)
        db = rt.orch.db
        return {**db.usage_summary(run_id), "calls": db.llm_calls(run_id)}

    @app.get("/api/runs/{run_id}/audit")
    def run_audit(run_id: str) -> dict[str, Any]:
        rt.run(run_id)
        audit = AuditLog(rt.orch.db)
        return {"entries": audit.entries(run_id), "verification": asdict(audit.verify(run_id))}

    @app.get("/api/runs/{run_id}/approvals")
    def run_approvals(run_id: str) -> list[dict[str, Any]]:
        rt.run(run_id)
        return rt.orch.db.approvals(run_id)

    # ------------------------------------------------------------------ built dashboard (after the API routes)
    static_dir = dashboard_dir if dashboard_dir is not None else DEFAULT_DASHBOARD_DIR
    if static_dir.is_dir():
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="dashboard")

    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn forgeline.api.server:app_factory --factory`."""
    return create_app()
