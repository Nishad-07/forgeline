"""Orchestrator: starts, drives and resumes workflow runs. Shared by the CLI and the API server.

Run lifecycle (runs.status):
    created -> running -> awaiting_human (gate) -> running -> ... -> completed | rejected | aborted
                       -> paused_provider_unavailable  (every LLM provider down; resume later)
                       -> failed                        (unexpected error; resumable from the checkpoint)

Each run is a LangGraph thread (thread_id = run_id) checkpointed in SQLite after every node, so a run can
be resumed from another process, e.g. started in the CLI and approved from the dashboard.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from forgeline.config import PROVIDERS, Settings, get_settings
from forgeline.graph.build import build_graph
from forgeline.graph.nodes import WorkflowContext
from forgeline.llm.errors import AllProvidersUnavailable
from forgeline.llm.router import LLMRouter, build_router
from forgeline.policy import Policy
from forgeline.requirements import Requirement
from forgeline.store import AuditLog, Database
from forgeline.tools.sandbox import Sandbox
from forgeline.tools.workspace import init_target_repo

RouterFactory = Callable[[str, bool, Database], LLMRouter]
TERMINAL = {"completed", "rejected", "aborted"}


class GateError(ValueError):
    """A human decision does not fit the gate the run is waiting at."""


class Orchestrator:
    def __init__(
        self, settings: Settings | None = None, *, router_factory: RouterFactory | None = None,
        db: Database | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.policy = Policy.load(self.settings.policy_path)
        self.db = db or Database(self.settings.db_path)
        self.audit = AuditLog(self.db)
        self.repo = init_target_repo(self.settings.root / "sample_repo", self.settings.workspace_dir / "target_repo")
        runs_dir = self.settings.workspace_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = Sandbox(runs_dir, self.policy.sandbox.allowed_commands, self.policy.sandbox.command_timeout_s)
        self.settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._ckpt_conn = sqlite3.connect(self.settings.checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._ckpt_conn)
        self._router_factory = router_factory or (
            lambda provider, cache, db: build_router(self.settings, db, primary=provider, cache_enabled=cache)
        )
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._cancelled: set[str] = set()
        self._cancel_actor: dict[str, str] = {}

    # ------------------------------------------------------------------ public API
    def start(
        self, requirement_path: str | Path, *, provider: str | None = None, cache: bool | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = self.create(requirement_path, provider=provider, cache=cache, run_id=run_id)
        return self.drive(run_id)

    def create(
        self, requirement_path: str | Path | None = None, *, requirement_text: str | None = None,
        title: str | None = None, provider: str | None = None, cache: bool | None = None,
        run_id: str | None = None,
    ) -> str:
        """Register a run from a requirement file or from free text typed by a user."""
        if (requirement_path is None) == (requirement_text is None):
            raise ValueError("provide exactly one of requirement_path or requirement_text")
        provider = (provider or self.settings.default_provider).lower()
        if provider not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        if requirement_text is not None:
            requirement, stored_path = self._requirement_from_text(requirement_text, title, stamp)
        else:
            path = Path(requirement_path)  # type: ignore[arg-type]
            if not path.is_absolute():
                path = self.settings.root / path
            requirement = Requirement.load(path)
            stored_path = (str(path.relative_to(self.settings.root)) if path.is_relative_to(self.settings.root)
                           else str(path))
        run_id = run_id or f"{requirement.id.lower()}-{stamp}-{secrets.token_hex(2)}"
        self.db.create_run(
            run_id, requirement_id=requirement.id, title=requirement.title,
            requirement_path=stored_path,
            provider=provider, status="created",
            data={"cache": self.settings.cache_enabled if cache is None else cache,
                  "requirement": asdict(requirement)},
        )
        self.db.add_event(run_id, "run_created", node=None, requirement=requirement.id, provider=provider)
        return run_id

    def drive(self, run_id: str, command: Any = None) -> dict[str, Any]:
        """Run the graph until it finishes, pauses at a human gate, or cannot continue."""
        with self._lock(run_id):
            run = self._run(run_id)
            graph = self._graph(run)
            config = self._config(run_id)
            snapshot = graph.get_state(config)
            if command is None and not snapshot.values:
                command = {"run_id": run_id, "requirement": run["data"]["requirement"], "provider": run["provider"]}
            if run_id in self._cancelled:  # stopped while waiting to start
                return self._finish_cancel(run_id)
            self.db.update_run(run_id, status="running", gate=None, error=None)
            try:
                for _ in graph.stream(command, config, stream_mode="updates"):
                    if run_id in self._cancelled:  # stop after the step that just finished
                        return self._finish_cancel(run_id)
            except AllProvidersUnavailable as exc:
                self.db.update_run(run_id, status="paused_provider_unavailable", error=str(exc)[:1000])
                self.db.add_event(run_id, "run_paused", node=None, reason="all LLM providers unavailable",
                                  error=str(exc)[:500])
                self.audit.append(run_id, "system", "run_paused", {"reason": "providers_unavailable"})
                return self.status(run_id)
            except Exception as exc:  # noqa: BLE001 - any unexpected error marks the run failed (resumable)
                self.db.update_run(run_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:1000]}")
                self.db.add_event(run_id, "run_failed", node=None, error=f"{type(exc).__name__}: {str(exc)[:500]}")
                return self.status(run_id)
            snapshot = graph.get_state(config)
            if snapshot.interrupts:
                gate = snapshot.interrupts[0].value
                self.db.update_run(run_id, status="awaiting_human", gate=gate)
                self.db.add_event(run_id, "gate_opened", node=None, gate=gate)
            elif not snapshot.next:
                outcome = snapshot.values.get("outcome", "completed")
                status = "completed" if outcome == "merged" else outcome
                self.db.update_run(run_id, status=status, outcome=outcome)
                self.db.add_event(run_id, "run_finished", node=None, outcome=outcome)
            return self.status(run_id)

    def cancel(self, run_id: str, *, actor: str = "human") -> dict[str, Any]:
        """Stop an unfinished run. A run that is executing stops after its current step; any other
        (waiting at a gate, paused, failed, or abandoned by a crashed process) is closed immediately."""
        run = self._run(run_id)
        if run["status"] in TERMINAL:
            raise GateError(f"run {run_id} is already {run['status']}")
        self._cancel_actor[run_id] = actor
        self._cancelled.add(run_id)
        lock = self._lock(run_id)
        if lock.acquire(blocking=False):  # nothing is executing it in this process
            try:
                return self._finish_cancel(run_id)
            finally:
                lock.release()
        return self.status(run_id)  # drive() will finish the cancellation after the current step

    def _finish_cancel(self, run_id: str) -> dict[str, Any]:
        self._cancelled.discard(run_id)
        actor = self._cancel_actor.pop(run_id, "human")
        self.db.update_run(run_id, status="aborted", outcome="cancelled", gate=None, error=None)
        self.db.record_approval(run_id, "run", "cancelled", actor, "run stopped by a human")
        self.db.add_event(run_id, "run_cancelled", node=None, actor=actor)
        self.audit.append(run_id, f"human:{actor}", "run_cancelled", {})
        return self.status(run_id)

    def resume(self, run_id: str, decision: dict[str, Any] | None = None, *, provider: str | None = None) -> dict[str, Any]:
        """Continue a run: answer its gate (`decision`), or retry after a pause/failure (no decision)."""
        return self.drive(run_id, self.prepare_resume(run_id, decision, provider=provider))

    def prepare_resume(
        self, run_id: str, decision: dict[str, Any] | None = None, *, provider: str | None = None
    ) -> Any:
        """Validate a resume request (and apply a provider switch) without running the graph.

        Returns the command to pass to `drive()`: a LangGraph `Command` answering the open gate, or None to
        retry from the last checkpoint. Raises GateError when the decision does not fit the gate.
        """
        run = self._run(run_id)
        if run["status"] in TERMINAL:
            raise GateError(f"run {run_id} is already {run['status']}")
        if provider:
            if provider not in PROVIDERS:
                raise ValueError(f"provider must be one of {PROVIDERS}")
            self.db.update_run(run_id, provider=provider)
            self.db.add_event(run_id, "provider_switched", node=None, provider=provider)
        gate = run["data"].get("gate") if run["status"] == "awaiting_human" else None
        if gate is not None:
            if decision is None:
                raise GateError(f"run {run_id} is waiting for a human decision at gate '{gate['gate']}'")
            self._validate(gate, decision)
            self.db.add_event(run_id, "gate_answered", node=None, gate=gate["gate"],
                              decision=decision.get("decision", "answered"), actor=decision.get("actor", "human"))
            return Command(resume=decision)
        if decision is not None:
            raise GateError(f"run {run_id} is not waiting at a gate (status: {run['status']})")
        return None

    def status(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        return {
            "id": run["id"], "status": run["status"], "stage": run["data"].get("stage"),
            "provider": run["provider"], "requirement_id": run["requirement_id"], "title": run["title"],
            "gate": run["data"].get("gate") if run["status"] == "awaiting_human" else None,
            "error": run["data"].get("error"), "outcome": run["data"].get("outcome"),
        }

    def state(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        snapshot = self._graph(run).get_state(self._config(run_id))
        return {"values": snapshot.values, "next": list(snapshot.next)}

    # ------------------------------------------------------------------ internals
    def _requirement_from_text(self, text: str, title: str | None, stamp: str) -> tuple[Requirement, str]:
        text = text.strip()
        if len(text) < 15:
            raise ValueError("the requirement is too short; describe what should change")
        parsed = Requirement.from_text(text)
        req_id = parsed.id if parsed.id not in {"", "REQ"} else f"REQ-{secrets.token_hex(2).upper()}"
        heading = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), "Requirement")
        resolved_title = (title or "").strip() or (parsed.title if parsed.title != "Untitled requirement"
                                                   else heading[:80])
        requirement = replace(parsed, id=req_id, title=resolved_title)
        folder = self.settings.data_dir / "requirements"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{req_id.lower()}-{stamp}.md"
        path.write_text(f"---\nid: {req_id}\ntitle: {resolved_title}\n---\n\n{requirement.body}\n", encoding="utf-8")
        requirement = replace(requirement, path=path.as_posix())
        return requirement, path.as_posix()

    @staticmethod
    def _validate(gate: dict[str, Any], decision: dict[str, Any]) -> None:
        kind = gate.get("gate")
        if kind == "clarification":
            answers = decision.get("answers")
            if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
                raise GateError("clarification needs {'answers': [str, ...]}")
            return
        choice = decision.get("decision")
        if kind == "release_approval":
            if choice not in {"approve", "request_changes", "reject"}:
                raise GateError("release decision must be approve, request_changes or reject")
            if choice == "request_changes" and not decision.get("comment"):
                raise GateError("request_changes needs a comment describing the changes")
            return
        if kind == "escalation":
            options = gate.get("options", ["retry", "abort"])
            if choice not in options:
                raise GateError(f"escalation decision must be one of {options}")
            if choice == "override" and not decision.get("comment"):
                raise GateError("a security override needs a written justification (comment)")
            return
        raise GateError(f"unknown gate {kind!r}")

    def _graph(self, run: dict[str, Any]) -> Any:
        router = self._router_factory(run["provider"], bool(run["data"].get("cache", True)), self.db)
        ctx = WorkflowContext(self.settings, self.policy, self.db, router, self.repo, self.sandbox)
        return build_graph(ctx, self.checkpointer)

    @staticmethod
    def _config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 150}

    def _run(self, run_id: str) -> dict[str, Any]:
        run = self.db.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        return run

    def _lock(self, run_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(run_id, threading.Lock())
