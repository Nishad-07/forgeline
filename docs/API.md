# Forgeline HTTP API contract

The FastAPI server (`forgeline/api/server.py`, started with `forgeline serve`, default `http://127.0.0.1:8000`) wraps
`forgeline.orchestrator.Orchestrator`. The React dashboard (`dashboard/`) consumes only these endpoints.
All JSON. All paths are prefixed with `/api`. The built dashboard (`dashboard/dist`) is served at `/`.

## Endpoints

| Method | Path | Response |
|---|---|---|
| GET | `/api/health` | `{"status": "ok", "version": "0.1.0"}` |
| GET | `/api/providers` | `{"default": "gemini", "failover": true, "providers": [{"name": "gemini", "available": true, "detail": "key present", "models": {"light": "...", "code": "..."}}, {"name": "ollama", ...}]}` |
| GET | `/api/policy` | `{"sha256": "...", "policy": {...policy.yaml as JSON...}}` |
| GET | `/api/requirements` | `[{"path": "requirements/partial_refund.md", "id": "REQ-101", "title": "Partial refunds", "priority": "high", "body": "markdown"}]` |
| GET | `/api/graph` | `{"nodes": [{"id": "intake", "label": "Intake", "kind": "system|agent|human|guard"}], "edges": [{"source": "a", "target": "b", "label": "optional"}]}` (static workflow topology for the graph view) |
| GET | `/api/runs` | `[RunSummary]` newest first |
| POST | `/api/runs` | body `{"requirement_text": "free text (markdown allowed)", "title": "optional", "provider": "gemini"|"ollama", "cache": true}` **or** `{"requirement_path": "requirements/partial_refund.md", ...}` -> `202 RunSummary`. Exactly one of `requirement_text` / `requirement_path`. The run executes in a background thread. |
| GET | `/api/runs/{id}` | `RunDetail` |
| GET | `/api/runs/{id}/events?after=0` | `[Event]` (ascending id) |
| GET | `/api/runs/{id}/stream` | Server-Sent Events. Each event: `event: <kind>`, `data: <Event JSON>`. Also `event: status` with `RunSummary` whenever status/stage changes, and a `ping` comment every 15 s. Query `?after=<event id>` to resume. |
| POST | `/api/runs/{id}/resume` | body `{"decision": {...} | null, "provider": "gemini"|"ollama"|null}` -> `202 RunSummary`. `409 {"detail": "..."}` when the decision does not fit the gate (GateError) or the run is finished. Executes in the background. |
| POST | `/api/runs/{id}/cancel` | body `{"actor": "name"}` (optional) -> `200 RunSummary`. Stops a run that is not finished (running, created, awaiting a human gate, paused or failed). A run that is executing stops after its current step; the final status is `aborted` with `outcome: "cancelled"`. `409` if the run is already finished. |
| GET | `/api/runs/{id}/diff` | `{"diff": "unified diff text", "stat": {"files": 3, "added": 40, "removed": 5}}` |
| GET | `/api/runs/{id}/usage` | `{"by_agent": [{"agent", "provider", "model", "calls", "input_tokens", "output_tokens", "latency_ms", "cost_usd", "cache_hits", "failures"}], "totals": {...same numeric keys...}, "calls": [LLMCall]}` |
| GET | `/api/runs/{id}/audit` | `{"entries": [{"seq", "ts", "actor", "action", "details", "prev_hash", "hash"}], "verification": {"ok": true, "entries": 12, "broken_at_seq": null, "reason": ""}}` |
| GET | `/api/runs/{id}/approvals` | `[{"ts", "gate", "decision", "actor", "comment"}]` |

## Types

```ts
type RunStatus = "created" | "running" | "awaiting_human" | "paused_provider_unavailable"
               | "failed" | "completed" | "rejected" | "aborted";

interface RunSummary {
  id: string; requirement_id: string; title: string; provider: "gemini" | "ollama";
  status: RunStatus; stage: string | null; outcome: string | null; error: string | null;
  created_at: string; updated_at: string;
  gate: Gate | null;            // present when status === "awaiting_human"
  stalled: boolean;             // true when status is "created"/"running" but nothing is executing it any more
                                // (e.g. the server was restarted) and it has not updated for over 2 minutes
}

type Gate =
  | { gate: "clarification"; questions: string[]; requirement: string; assumptions: string[] }
  | { gate: "release_approval"; headline: string; recommendation: "approve" | "request_changes" | "reject";
      reason: string; branch: string; commit: string; facts: Record<string, unknown>;
      scores: { golden_pass_rate: number | null; rubric_percent: number | null; overall: number | null } | null;
      known_risks: string[] }
  | { gate: "escalation"; reason: "malformed_output" | "retry_budget_exhausted" | "security_block" | "design_deadlock";
      node: string; details: string; resume_node: string; options: ("retry" | "override" | "abort")[] };

// Decisions sent to POST /resume as `decision`:
//   clarification:     {"answers": ["...", ...], "actor": "name"}
//   release_approval:  {"decision": "approve" | "request_changes" | "reject", "comment": "...", "actor": "name"}
//                      (request_changes requires a comment)
//   escalation:        {"decision": "retry" | "override" | "abort", "comment": "...", "actor": "name"}
//                      (override requires a comment = written justification)
// After a pause (paused_provider_unavailable) or failure: {"decision": null, "provider": "ollama"} retries.

interface RunDetail extends RunSummary {
  requirement: { id: string; title: string; body: string; path: string; priority: string };
  state: {            // LangGraph checkpoint values (all optional; filled as the run progresses)
    analysis?: { summary: string; acceptance_criteria: {id: string; given: string; when: string; then: string}[];
                 tasks: {id: string; title: string; description: string}[]; clarifying_questions: string[];
                 assumptions: string[]; out_of_scope: string[]; ambiguity_score: number };
    clarifications?: { question: string; answer: string }[];
    design?: { summary: string; impacted_components: {path: string; change_type: "modify" | "create"; reason: string}[];
               context_files: string[]; untouched_components: string[]; design_notes: string;
               test_strategy: string; risks: string[] };
    impact_warnings?: string[];
    acceptance?: { path: string; covered_criteria: string[]; baseline: {passed: number; failed: number; errors: number};
                   warning: string; content: string };
    dev_attempt?: number; dev_budget?: number; feedback?: string[];
    scope?: { ok: boolean; files?: string[]; violations: {path: string; rule: string; reason: string}[] };
    qa?: { ok: boolean; dev_files: string[];
           tests: { passed: number; failed: number; errors: number; skipped: number; total: number; ok: boolean;
                    coverage_percent: number | null; coverage_by_file: Record<string, number>;
                    cases: {nodeid: string; outcome: string; duration_s: number; message: string}[] };
           lint: { ok: boolean; issues: {file: string; line: number; code: string; message: string}[] } };
    security?: { findings: Finding[]; blocking: Finding[]; introduced_count: number;
                 triage: { summary: string; assessments: {rule: string; file: string; line: number;
                           verdict: string; explanation: string}[];
                           additional_concerns: {severity: string; file: string; line: number; description: string}[] } | null };
    security_fix_attempts?: number; security_override?: { actor: string; justification: string; ts: string };
    attempts_log?: { attempt: number; ts: string; stage: "scope" | "qa"; scope_ok: boolean; passed?: number;
                     failed?: number; errors?: number; coverage?: number; lint_issues?: number;
                     violations?: {path: string; rule: string; reason: string}[] }[];
    evaluation?: { golden: {available: boolean; passed: number; total: number; failures: string[]};
                   rubric: { items: {criterion: string; score: number; justification: string}[]; overall_comment: string } | null;
                   scores: { golden_pass_rate: number | null; rubric_percent: number | null; overall: number | null };
                   reference_files: string[]; agent_files: string[] };
    release?: { headline: string; change_summary: string; recommendation: string; recommendation_reason: string;
                criteria_evidence: {criterion_id: string; status: "met" | "not_met" | "unverified"; evidence: string}[];
                known_risks: string[]; rollback_plan: string; facts: Record<string, unknown>;
                diff_stat: {files: number; added: number; removed: number}; branch: string; commit: string };
    human_decisions?: { gate: string; decision?: string; actor: string; comment?: string; ts: string }[];
    outcome?: "merged" | "rejected" | "aborted";
  };
  next: string[];     // next LangGraph node(s), e.g. ["approval"] while waiting
}

interface Finding { rule: string; severity: "critical" | "high" | "medium" | "low"; file: string; line: number;
                    message: string; evidence: string; source: "custom" | "bandit"; introduced: boolean }

interface Event { id: number; run_id: string; ts: string; node: string | null; kind: string; payload: Record<string, unknown> }

interface LLMCall { id: number; ts: string; agent: string; provider: string; model: string; tier: string;
                    input_tokens: number; output_tokens: number; latency_ms: number; cost_usd: number;
                    cached: 0 | 1; attempt: number; status: "ok" | "repaired" | "cached" | "invalid_output" | "provider_error";
                    error: string | null }
```

## Event kinds (`Event.kind`)

| kind | node | payload |
|---|---|---|
| `run_created` | - | `requirement, provider` |
| `node_start` / `node_end` | node id | `node_end`: `duration_ms, summary` |
| `node_error` | node id | `error` |
| `llm_call` | agent | `provider, model, latency_ms, input_tokens, output_tokens, repairs, cached?` |
| `llm_delta` | agent | `call, provider, model, text`: live token stream. `text` is the text generated since the previous delta of the same `call`; append in order. A new `call` id (e.g. a repair retry) starts a fresh stream for that agent. High volume: render in the live output panel, not in the timeline. |
| `agent_output` | agent | `agent, output`: the agent's final validated output (shape = the agent's schema: analyst `analysis`, architect `design`, qa `{content, covered_criteria}`, developer `{files: [{path, edits: [{find, replace}], content}], rationale, design_objection}`, security `triage`, release `release` summary, evaluator rubric). Emitted once per successful agent run; replaces the streamed text in the UI. |
| `llm_retry` | agent | `provider, delay_s, error` |
| `llm_repair` | agent | `provider, repair, error` (malformed JSON re-prompt) |
| `llm_failover` / `llm_provider_failed` | agent | `from, to, error` |
| `agent_output_rejected` | agent | `attempt, problems[]` (semantic check failed) |
| `agent_escalation` | agent | `from, to, problems[]` (agent moved to the next provider) |
| `scope_violation` | apply_change | `violations[]` |
| `repeated_action_blocked` / `duplicate_action_skipped` | apply_change | - |
| `qa_tests_rejected` | qa_author | `error` |
| `gate_opened` | - | `gate` (Gate object) |
| `gate_answered` | - | `gate, decision, actor` |
| `provider_switched` | - | `provider` |
| `run_paused` | - | `reason, error` |
| `run_failed` | - | `error` |
| `run_finished` | - | `outcome` |

## Workflow nodes (for the graph view)

`intake` (system) -> `analyst` (agent) -> `clarify` (human) -> `architect` (agent) -> `reconcile` (system) ->
`qa_author` (agent) -> `developer` (agent) -> `apply_change` (guard: scope guard) -> `qa_run` (agent: tests+lint) ->
`security` (agent) -> `evaluate` (agent) -> `release` (agent) -> `approval` (human) -> `merge_deploy` (system);
`escalate` (human) and `close` (system). Edges are listed in `forgeline/graph/build.py`.
