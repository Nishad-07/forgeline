"""`forgeline chat`: an interactive terminal session with the agents.

Type a requirement in plain language; every agent's output streams live as the model writes it and is
then replaced by a formatted result. Human gates (clarification, release approval, escalations) are
answered inline. Works with Gemini or local Ollama.
"""

from __future__ import annotations

import sys
from typing import Any

import typer
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from forgeline.cli import EventPrinter, console, drive_interactive, format_event

AGENT_TITLES = {
    "analyst": "Analyst: acceptance criteria and plan",
    "architect": "Architect: impact analysis and design",
    "qa": "QA: acceptance tests (written before the code)",
    "developer": "Developer: code change",
    "security": "Security: review",
    "evaluator": "Evaluator: rubric",
    "release": "Release: evidence for the approver",
}
TOOL_NODES = {"apply_change": "Scope guard", "qa_run": "Tests + lint", "evaluate": "Evaluation",
              "intake": "Workspace", "merge_deploy": "Merge + deploy"}


class ChatPrinter(EventPrinter):
    """Streams `llm_delta` tokens inline and renders `agent_output` as formatted panels."""

    def __init__(self, db, run_id: str) -> None:
        super().__init__(db, run_id)
        self.streaming_call: str | None = None

    def _end_stream(self) -> None:
        if self.streaming_call is not None:
            console.print()
            self.streaming_call = None

    def flush(self) -> None:
        for event in self.db.events(self.run_id, after_id=self.last):
            self.last = event["id"]
            kind, node, p = event["kind"], event.get("node") or "", event.get("payload") or {}
            if kind == "llm_delta":
                if p.get("call") != self.streaming_call:
                    self._end_stream()
                    console.print(Text(f"  {node} is writing ({p.get('provider')}/{p.get('model')})",
                                       style="bold green"))
                    self.streaming_call = p.get("call")
                console.print(Text(p.get("text", ""), style="dim"), end="", soft_wrap=True)
                continue
            self._end_stream()
            if kind == "agent_output":
                render_agent_output(p.get("agent", node), p.get("output") or {})
            elif kind == "node_start":
                if node in TOOL_NODES or node in {"clarify", "approval", "escalate"}:
                    continue
                console.print(f"[bold cyan]> {node}[/]")
            elif kind == "node_end" and node in TOOL_NODES:
                console.print(f"[bold]{TOOL_NODES[node]}[/]  {p.get('summary', '')}")
            elif kind in {"node_end", "llm_call", "llm_delta"}:
                continue
            else:
                line = format_event(event)
                if line:
                    console.print(line)


def _criteria_table(criteria: list[dict[str, Any]]) -> Table:
    table = Table(show_header=True, header_style="bold", expand=True)
    for col in ("id", "given", "when", "then"):
        table.add_column(col, overflow="fold")
    for c in criteria:
        table.add_row(c.get("id", ""), c.get("given", ""), c.get("when", ""), c.get("then", ""))
    return table


def render_agent_output(agent: str, out: dict[str, Any]) -> None:
    title = AGENT_TITLES.get(agent, agent)
    if agent == "analyst":
        console.print(Panel(out.get("summary", ""), title=title, border_style="green"))
        console.print(_criteria_table(out.get("acceptance_criteria", [])))
        for t in out.get("tasks", []):
            console.print(f"  [bold]{t.get('id')}[/] {t.get('title')}")
        for q in out.get("clarifying_questions", []):
            console.print(f"  [magenta]? {q}[/]")
    elif agent == "architect":
        lines = [out.get("summary", ""), ""]
        lines += [f"  [bold]{c['change_type']:6}[/] {c['path']}  [dim]{c.get('reason', '')}[/]"
                  for c in out.get("impacted_components", [])]
        if out.get("untouched_components"):
            lines.append(f"  [dim]untouched: {', '.join(out['untouched_components'])}[/]")
        for r in out.get("risks", []):
            lines.append(f"  [yellow]risk:[/] {r}")
        console.print(Panel("\n".join(lines), title=title, border_style="green"))
    elif agent == "qa":
        console.print(Panel(Syntax(out.get("content", ""), "python", line_numbers=False, word_wrap=True),
                            title=f"{title}  covers {', '.join(out.get('covered_criteria', []))}",
                            border_style="green"))
    elif agent == "developer":
        if out.get("design_objection"):
            console.print(Panel(out["design_objection"], title="Developer objects to the design", border_style="yellow"))
            return
        console.print(Panel(out.get("rationale", ""), title=title, border_style="green"))
        for f in out.get("files", []):
            console.print(f"  [bold]{f['path']}[/]")
            for e in f.get("edits", []):
                for line in e.get("find", "").splitlines():
                    console.print(Text(f"    - {line}", style="red"))
                for line in e.get("replace", "").splitlines():
                    console.print(Text(f"    + {line}", style="green"))
            if f.get("content"):
                console.print(Text(f"    (complete file, {len(f['content'].splitlines())} lines)", style="dim"))
    elif agent == "security":
        lines = [out.get("summary", "")]
        lines += [f"  {a['verdict']:14} {a['rule']} {a['file']}:{a['line']}  {a['explanation']}"
                  for a in out.get("assessments", [])]
        lines += [f"  [yellow]{c['severity']}[/] {c['file']}:{c.get('line', 0)} {c['description']}"
                  for c in out.get("additional_concerns", [])]
        console.print(Panel("\n".join(lines), title=title, border_style="green"))
    elif agent == "evaluator":
        lines = [f"  {i['criterion']:17} {i['score']}/5  {i['justification']}" for i in out.get("items", [])]
        console.print(Panel("\n".join(lines + ["", out.get("overall_comment", "")]), title=title, border_style="green"))
    elif agent == "release":
        lines = [f"[bold]{out.get('headline', '')}[/]", out.get("change_summary", ""), "",
                 f"Recommendation: [bold]{out.get('recommendation')}[/]  {out.get('recommendation_reason', '')}"]
        lines += [f"  {e['criterion_id']}: {e['status']}  [dim]{e['evidence']}[/]" for e in out.get("criteria_evidence", [])]
        console.print(Panel("\n".join(lines), title=title, border_style="green"))


def read_requirement() -> str:
    console.print("[bold]Describe the requirement.[/] Paste or type it; finish with a line containing only [bold].[/]")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def chat(provider: str | None, text: str | None, actor: str, no_cache: bool) -> None:
    from forgeline.orchestrator import Orchestrator

    orch = Orchestrator()
    console.print(Panel("Type a requirement and watch the agents work on the PayLite payments service.\n"
                        "You approve every merge. Ctrl+C to exit.", title="Forgeline agent session", border_style="green"))
    while True:
        requirement = text or read_requirement()
        if not requirement:
            console.print("[yellow]No requirement entered.[/]")
            return
        try:
            run_id = orch.create(requirement_text=requirement, provider=provider,
                                 cache=False if no_cache else None)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            if text:
                return
            continue
        run = orch.db.get_run(run_id)
        console.print(f"[dim]run {run_id} | {run['title']} | provider {run['provider']}[/]")
        with ChatPrinter(orch.db, run_id):
            status = orch.drive(run_id)
        drive_interactive(orch, status, True, actor, printer_cls=ChatPrinter)
        if text or not sys.stdin.isatty() or not typer.confirm("Another requirement?", default=False):
            return
