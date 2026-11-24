"""`forgeline` command-line interface.

    forgeline doctor                                  check tools, providers, workspace
    forgeline sample init [--reset]                   (re)create the target repository from sample_repo
    forgeline run requirements/partial_refund.md      start a run; streams live progress; prompts at human gates
    forgeline status [RUN_ID]                         list runs, or show one run
    forgeline answer RUN_ID -a "..." -a "..."         answer clarification questions
    forgeline approve RUN_ID [--reject | --request-changes "..."]
    forgeline decide RUN_ID retry|override|abort -c "..."   answer an escalation
    forgeline resume RUN_ID [--provider ollama]       continue after a provider outage or failure
    forgeline metrics RUN_ID / forgeline audit RUN_ID      usage + cost, tamper-evident audit log
    forgeline serve                                   API + dashboard on http://127.0.0.1:8000
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Self

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forgeline import __version__
from forgeline.config import ROOT, get_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Forgeline: Forgeline (multi-agent SDLC with human gates)")
sample_app = typer.Typer(help="Manage the sample target repository")
app.add_typer(sample_app, name="sample")
console = Console(highlight=False)

STATUS_STYLE = {
    "running": "cyan", "awaiting_human": "magenta", "completed": "green", "rejected": "yellow",
    "aborted": "yellow", "failed": "red", "paused_provider_unavailable": "red", "created": "white",
}


def _orchestrator():
    from forgeline.orchestrator import Orchestrator

    return Orchestrator()


# ------------------------------------------------------------------ live event printer
class EventPrinter:
    """Tails the run's events from SQLite and prints them while the graph runs in this process."""

    def __init__(self, db, run_id: str) -> None:
        self.db, self.run_id = db, run_id
        self.last = max((e["id"] for e in db.events(run_id)), default=0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.flush()
            time.sleep(0.4)

    def flush(self) -> None:
        for event in self.db.events(self.run_id, after_id=self.last):
            self.last = event["id"]
            line = format_event(event)
            if line:
                console.print(line)


def format_event(e: dict[str, Any]) -> str | None:
    kind, node, p = e["kind"], e.get("node") or "", e.get("payload") or {}
    if kind == "node_start":
        return f"[bold cyan]>[/] [bold]{node}[/]"
    if kind == "node_end":
        secs = (p.get("duration_ms") or 0) / 1000
        return f"  [green]done[/] {node} [dim]({secs:.1f}s)[/] {p.get('summary', '')}"
    if kind == "llm_call":
        if p.get("cached"):
            return f"  [dim]  llm {p.get('provider')}/{p.get('model')} cache hit[/]"
        return (f"  [dim]  llm {p.get('provider')}/{p.get('model')} {p.get('latency_ms', 0) / 1000:.1f}s "
                f"{p.get('input_tokens')}/{p.get('output_tokens')} tok"
                f"{' repaired x' + str(p['repairs']) if p.get('repairs') else ''}[/]")
    styles = {
        "llm_retry": ("yellow", "retry"), "llm_repair": ("yellow", "repair prompt"),
        "llm_failover": ("bold yellow", "FAILOVER"), "llm_provider_failed": ("red", "provider failed"),
        "agent_output_rejected": ("yellow", "output rejected"), "agent_escalation": ("bold yellow", "agent escalated"),
        "scope_violation": ("bold red", "SCOPE VIOLATION"), "repeated_action_blocked": ("red", "repeated action blocked"),
        "duplicate_action_skipped": ("yellow", "duplicate action skipped"), "qa_tests_rejected": ("yellow", "QA tests rejected"),
        "node_error": ("red", "error"), "run_paused": ("bold red", "RUN PAUSED"), "run_failed": ("bold red", "RUN FAILED"),
        "provider_switched": ("cyan", "provider switched"), "gate_answered": ("magenta", "gate answered"),
    }
    if kind in styles:
        style, label = styles[kind]
        detail = {k: v for k, v in p.items() if k not in {"error"}} or ""
        err = f" {str(p.get('error'))[:160]}" if p.get("error") else ""
        return f"  [{style}]! {label}[/] [dim]{node}[/] {detail}{err}"
    if kind == "gate_opened":
        return f"[bold magenta]|| human gate: {(p.get('gate') or {}).get('gate')}[/]"
    if kind == "run_finished":
        return f"[bold green]run finished: {p.get('outcome')}[/]"
    return None


# ------------------------------------------------------------------ gate rendering + prompting
def render_gate(status: dict[str, Any]) -> None:
    gate = status.get("gate") or {}
    kind = gate.get("gate")
    if kind == "clarification":
        body = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(gate.get("questions", [])))
        console.print(Panel(body, title="Clarification needed (analyst)", border_style="magenta"))
    elif kind == "release_approval":
        facts = gate.get("facts") or {}
        scores = gate.get("scores") or {}
        lines = [f"[bold]{gate.get('headline')}[/]",
                 f"Recommendation: [bold]{gate.get('recommendation')}[/] - {gate.get('reason')}",
                 f"Branch {gate.get('branch')} @ {str(gate.get('commit'))[:10]}", ""]
        lines += [f"  {k}: {v}" for k, v in facts.items()]
        if scores and scores.get("overall") is not None:
            golden = (f"hidden tests {scores['golden_pass_rate']}%, " if scores.get("golden_pass_rate") is not None
                      else "no hidden baseline for this requirement, ")
            lines.append(f"  evaluation: overall {scores['overall']} ({golden}rubric {scores.get('rubric_percent')}%)")
        for risk in gate.get("known_risks", []):
            lines.append(f"  risk: {risk}")
        console.print(Panel("\n".join(lines), title="Release approval required", border_style="magenta"))
    elif kind == "escalation":
        console.print(Panel(f"[bold]{gate.get('reason')}[/] at {gate.get('node')}\n{gate.get('details')}\n\n"
                            f"Options: {', '.join(gate.get('options', []))}",
                            title="Escalation: human decision required", border_style="red"))


def prompt_gate(status: dict[str, Any], actor: str) -> dict[str, Any] | None:
    gate = status.get("gate") or {}
    kind = gate.get("gate")
    if kind == "clarification":
        answers = [typer.prompt(f"Answer {i + 1}", default="Use your best judgement")
                   for i, _ in enumerate(gate.get("questions", []))]
        return {"answers": answers, "actor": actor}
    if kind == "release_approval":
        choice = typer.prompt("Decision [approve/request_changes/reject/later]", default="approve")
        if choice == "later":
            return None
        comment = typer.prompt("Comment", default="" if choice != "request_changes" else "please revise")
        return {"decision": choice, "comment": comment, "actor": actor}
    if kind == "escalation":
        options = gate.get("options", ["retry", "abort"])
        choice = typer.prompt(f"Decision [{'/'.join(options)}/later]", default=options[0])
        if choice == "later":
            return None
        comment = typer.prompt("Comment / guidance / justification", default="")
        return {"decision": choice, "comment": comment, "actor": actor}
    return None


def show_status(status: dict[str, Any]) -> None:
    style = STATUS_STYLE.get(status["status"], "white")
    console.print(f"run [bold]{status['id']}[/]  status [{style}]{status['status']}[/]  stage {status.get('stage')}  "
                  f"provider {status.get('provider')}")
    if status.get("error"):
        console.print(f"[red]{status['error'][:400]}[/]")


def next_steps(status: dict[str, Any]) -> None:
    rid = status["id"]
    gate = (status.get("gate") or {}).get("gate")
    hints = {
        "clarification": f'forgeline answer {rid} -a "..."',
        "release_approval": f"forgeline approve {rid}   |   forgeline approve {rid} --reject -c \"why\"",
        "escalation": f'forgeline decide {rid} retry|override|abort -c "..."',
    }
    if gate:
        console.print(f"[dim]next: {hints[gate]}[/]")
    elif status["status"] in {"paused_provider_unavailable", "failed"}:
        console.print(f"[dim]next: forgeline resume {rid} [--provider ollama|gemini][/]")


def drive_interactive(orch, status: dict[str, Any], interactive: bool, actor: str,
                      printer_cls: type[EventPrinter] | None = None) -> dict[str, Any]:
    printer_cls = printer_cls or EventPrinter
    while True:
        show_status(status)
        if status["status"] != "awaiting_human" or not interactive or not sys.stdin.isatty():
            if status["status"] == "awaiting_human":
                render_gate(status)
            next_steps(status)
            return status
        render_gate(status)
        try:
            decision = prompt_gate(status, actor)
        except (typer.Abort, EOFError):  # no usable terminal input: leave the run waiting at its gate
            console.print("[dim]No input available; the run stays at this gate.[/]")
            decision = None
        if decision is None:
            next_steps(status)
            return status
        from forgeline.orchestrator import GateError

        try:
            with printer_cls(orch.db, status["id"]):
                status = orch.resume(status["id"], decision)
        except GateError as exc:
            console.print(f"[red]{exc}[/]")


# ------------------------------------------------------------------ commands
@app.command("chat")
def chat_command(
    provider: str = typer.Option(None, "--provider", "-p", help="gemini | ollama (default from .env)"),
    text: str = typer.Option(None, "--text", "-t", help="Requirement text (skips the interactive prompt)"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable the LLM response cache"),
    actor: str = typer.Option("human", "--actor", help="Your name for the audit log"),
) -> None:
    """Interactive session: type a requirement and watch every agent's output live."""
    from forgeline.chat import chat

    chat(provider, text, actor, no_cache)


@app.command()
def run(
    requirement: Path = typer.Argument(..., help="Requirement markdown file"),
    provider: str = typer.Option(None, "--provider", "-p", help="gemini | ollama (default from .env)"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable the LLM response cache"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="Prompt at human gates"),
    actor: str = typer.Option("human", "--actor", help="Your name for the audit log"),
) -> None:
    """Start a workflow run for REQUIREMENT."""
    orch = _orchestrator()
    run_id = orch.create(requirement, provider=provider, cache=False if no_cache else None)
    console.print(Panel(f"Run [bold]{run_id}[/]\nrequirement {requirement}\nprovider {orch.db.get_run(run_id)['provider']}"
                        f"  failover {'on' if orch.settings.failover else 'off'}", title="Forgeline", border_style="cyan"))
    with EventPrinter(orch.db, run_id):
        status = orch.drive(run_id)
    drive_interactive(orch, status, interactive, actor)


@app.command()
def answer(run_id: str, answers: list[str] = typer.Option(..., "--answer", "-a", help="One per question, in order"),
           actor: str = typer.Option("human", "--actor"),
           interactive: bool = typer.Option(True, "--interactive/--no-interactive")) -> None:
    """Answer the analyst's clarification questions."""
    _resume(run_id, {"answers": answers, "actor": actor}, interactive=interactive, actor=actor)


@app.command()
def approve(
    run_id: str,
    reject: bool = typer.Option(False, "--reject", help="Reject the release"),
    request_changes: str = typer.Option(None, "--request-changes", help="Send back to the developer with this comment"),
    comment: str = typer.Option("", "--comment", "-c"),
    actor: str = typer.Option("human", "--actor"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive"),
) -> None:
    """Approve (merge + mock deploy), request changes, or reject a release."""
    if request_changes:
        decision = {"decision": "request_changes", "comment": request_changes, "actor": actor}
    else:
        decision = {"decision": "reject" if reject else "approve", "comment": comment, "actor": actor}
    _resume(run_id, decision, interactive=interactive, actor=actor)


@app.command()
def decide(run_id: str, decision: str = typer.Argument(..., help="retry | override | abort"),
           comment: str = typer.Option("", "--comment", "-c", help="Guidance, or justification for override"),
           actor: str = typer.Option("human", "--actor"),
           interactive: bool = typer.Option(True, "--interactive/--no-interactive")) -> None:
    """Answer an escalation (retry budget exhausted, security block, design deadlock, malformed output)."""
    _resume(run_id, {"decision": decision, "comment": comment, "actor": actor}, interactive=interactive, actor=actor)


@app.command()
def resume(run_id: str, provider: str = typer.Option(None, "--provider", "-p"),
           interactive: bool = typer.Option(True, "--interactive/--no-interactive"),
           actor: str = typer.Option("human", "--actor")) -> None:
    """Continue a paused/failed run from its last checkpoint (optionally on another provider)."""
    _resume(run_id, None, provider=provider, interactive=interactive, actor=actor)


def _resume(run_id: str, decision: dict[str, Any] | None, provider: str | None = None,
            interactive: bool = True, actor: str = "human") -> None:
    from forgeline.orchestrator import GateError

    orch = _orchestrator()
    try:
        with EventPrinter(orch.db, run_id):
            status = orch.resume(run_id, decision, provider=provider)
    except (GateError, KeyError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    drive_interactive(orch, status, interactive, actor)


@app.command()
def stop(run_id: str, actor: str = typer.Option("human", "--actor")) -> None:
    """Stop an unfinished run (running, waiting at a gate, paused or failed)."""
    from forgeline.orchestrator import GateError

    orch = _orchestrator()
    try:
        status = orch.cancel(run_id, actor=actor)
    except (GateError, KeyError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    show_status(status)


@app.command()
def status(run_id: str = typer.Argument(None)) -> None:
    """List runs, or show the timeline of one run."""
    from forgeline.store.db import Database

    db = Database(get_settings().db_path)
    if not run_id:
        table = Table(title="Runs")
        table.add_column("id", no_wrap=True)  # full id so it can be pasted into approve / stop / resume
        table.add_column("requirement", max_width=30, overflow="ellipsis", no_wrap=True)
        for col in ("provider", "status", "stage", "updated"):
            table.add_column(col, no_wrap=True)
        for r in db.list_runs():
            style = STATUS_STYLE.get(r["status"], "white")
            outcome = r["data"].get("outcome")
            label = "stopped" if outcome == "cancelled" else r["status"]
            table.add_row(r["id"], r["title"], r["provider"], f"[{style}]{label}[/]",
                          str(r["data"].get("stage") or ""), r["updated_at"][:19].replace("T", " "))
        console.print(table)
        return
    run = db.get_run(run_id)
    if run is None:
        console.print(f"[red]unknown run {run_id}[/]")
        raise typer.Exit(1)
    show_status({"id": run_id, "status": run["status"], "stage": run["data"].get("stage"),
                 "provider": run["provider"], "error": run["data"].get("error")})
    for event in db.events(run_id):
        line = format_event(event)
        if line:
            console.print(f"[dim]{event['ts'][11:19]}[/] {line}")
    if run["status"] == "awaiting_human":
        render_gate({"gate": run["data"].get("gate")})
        next_steps({"id": run_id, "status": run["status"], "gate": run["data"].get("gate")})


@app.command()
def metrics(run_id: str) -> None:
    """Model usage, latency and equivalent cost per agent."""
    from forgeline.store.db import Database

    db = Database(get_settings().db_path)
    usage = db.usage_summary(run_id)
    table = Table(title=f"LLM usage - {run_id}")
    for col in ("agent", "provider", "model", "calls", "tokens in/out", "latency s", "cost $", "cache", "failures"):
        table.add_column(col, justify="right" if col not in {"agent", "provider", "model"} else "left")
    for r in usage["by_agent"]:
        table.add_row(r["agent"], r["provider"], r["model"], str(r["calls"]),
                      f"{r['input_tokens']}/{r['output_tokens']}", f"{(r['latency_ms'] or 0) / 1000:.1f}",
                      f"{r['cost_usd'] or 0:.5f}", str(r["cache_hits"]), str(r["failures"]))
    t = usage["totals"]
    table.add_row("[bold]total[/]", "", "", str(t["calls"]), f"{t['input_tokens']}/{t['output_tokens']}",
                  f"{(t['latency_ms'] or 0) / 1000:.1f}", f"{t['cost_usd'] or 0:.4f}", str(t["cache_hits"]),
                  str(t["failures"]))
    console.print(table)
    console.print("[dim]Equivalent cost at list prices (pricing.yaml); the actual bill on free tier / local is $0.[/]")


@app.command()
def audit(run_id: str) -> None:
    """Show and verify the run's hash-chained audit log."""
    from forgeline.store import AuditLog
    from forgeline.store.db import Database

    log = AuditLog(Database(get_settings().db_path))
    table = Table(title=f"Audit log - {run_id}")
    for col in ("seq", "time", "actor", "action", "hash"):
        table.add_column(col)
    for e in log.entries(run_id):
        table.add_row(str(e["seq"]), e["ts"][11:19], e["actor"], e["action"], e["hash"][:12])
    console.print(table)
    result = log.verify(run_id)
    if result.ok:
        console.print(f"[green]chain intact: {result.entries} entries verified[/]")
    else:
        console.print(f"[bold red]TAMPERED at seq {result.broken_at_seq}: {result.reason}[/]")
        raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """Check that everything needed for a live run is in place."""
    settings = get_settings()
    rows: list[tuple[str, bool, str]] = []
    rows.append(("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    rows.append(("git", shutil.which("git") is not None, shutil.which("git") or "not found"))
    for tool in ("pytest", "ruff", "bandit"):
        proc = subprocess.run([sys.executable, "-m", tool, "--version"], capture_output=True, text=True, check=False)
        rows.append((tool, proc.returncode == 0, (proc.stdout or proc.stderr).strip().splitlines()[0][:60]))
    rows.append(("gemini key", settings.gemini_key_present,
                 f"{settings.gemini.light} / {settings.gemini.code}" if settings.gemini_key_present else "GOOGLE_API_KEY missing"))
    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).json()
        installed = {m["name"] for m in tags.get("models", [])}
        wanted = {settings.ollama.light, settings.ollama.code}
        missing = wanted - installed
        rows.append(("ollama", not missing, "missing " + ", ".join(sorted(missing)) if missing else ", ".join(sorted(wanted))))
    except httpx.HTTPError as exc:
        rows.append(("ollama", False, f"not reachable at {settings.ollama_base_url} ({type(exc).__name__})"))
    from forgeline.policy import Policy

    policy = Policy.load(settings.policy_path)
    rows.append(("policy", True, f"{settings.policy_path.name} sha256 {policy.sha256[:12]}"))
    target = settings.workspace_dir / "target_repo"
    rows.append(("target repo", (target / ".git").exists(), str(target) if (target / ".git").exists() else "run: forgeline sample init"))
    dist = ROOT / "dashboard" / "dist" / "index.html"
    rows.append(("dashboard build", dist.exists(), "built" if dist.exists() else "cd dashboard; npm install; npm run build"))
    table = Table(title=f"forgeline doctor (v{__version__})")
    for col in ("check", "ok", "detail"):
        table.add_column(col)
    for name, ok, detail in rows:
        table.add_row(name, "[green]yes[/]" if ok else "[red]no[/]", detail)
    console.print(table)
    console.print(f"default provider: [bold]{settings.default_provider}[/]  failover: {settings.failover}  "
                  f"developer output: {settings.dev_output_mode}")


@sample_app.command("init")
def sample_init(reset: bool = typer.Option(False, "--reset", help="Delete all runs and recreate the target repo")) -> None:
    """Create the target git repository (workspace/target_repo) from sample_repo/."""
    from forgeline.tools.workspace import init_target_repo, reset_workspace

    settings = get_settings()
    if reset:
        reset_workspace(settings.workspace_dir)
        for name in ("forgeline.db", "forgeline.db-wal", "forgeline.db-shm", "checkpoints.db", "checkpoints.db-wal",
                     "checkpoints.db-shm"):
            (settings.data_dir / name).unlink(missing_ok=True)
    repo = init_target_repo(ROOT / "sample_repo", settings.workspace_dir / "target_repo")
    console.print(f"[green]target repo ready[/] {repo.path} @ {repo.head()[:10]}")


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8000)) -> None:
    """Start the API server and dashboard."""
    import uvicorn

    console.print(f"Forgeline dashboard: [bold]http://{host}:{port}[/]")
    uvicorn.run("forgeline.api.server:create_app", factory=True, host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
