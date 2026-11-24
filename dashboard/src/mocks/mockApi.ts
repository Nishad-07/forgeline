// In-memory mock of the Forgeline API (VITE_MOCK=1). Serves fixtures, validates gate decisions like the
// real orchestrator (409 on mismatch) and replays scripted events so the live views animate.

import { ApiError } from "../api/client";
import type {
  Approval,
  AuditEntry,
  CreateRunBody,
  DiffResponse,
  Evaluation,
  Gate,
  LLMCall,
  ProviderName,
  ReleaseApprovalGate,
  ReleaseEvidence,
  ResumeBody,
  RunDetail,
  RunEvent,
  RunState,
  RunStatus,
  RunSummary,
  ScopeViolation,
  UsageByAgent,
  UsageNumbers,
  UsageResponse,
} from "../types";
import {
  req101ApprovalGate,
  req101AuditSeeds,
  req101CallSeeds,
  req101Diff,
  req101Script,
  req101State,
  req101UsageByAgent,
  type ScriptStep,
} from "./req101";
import { req102Diff, req102EscalationGate, req102Script, req102State, req102UsageByAgent } from "./req102";
import { buildConsoleRun } from "./consoleScript";
import { graph, policy, providers, requirements } from "./static";

// ------------------------------------------------------------------ helpers

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const iso = (ms: number) => new Date(ms).toISOString();

function fakeHash(input: string): string {
  let out = "";
  for (let seed = 0; seed < 8; seed++) {
    let h = 0x811c9dc5 ^ (seed * 0x9e3779b1);
    for (let i = 0; i < input.length; i++) {
      h ^= input.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    out += (h >>> 0).toString(16).padStart(8, "0");
  }
  return out;
}

interface MockRun {
  detail: RunDetail;
  events: RunEvent[];
  diff: DiffResponse;
  usageAgents: UsageByAgent[];
  calls: LLMCall[];
  audit: AuditEntry[];
  approvals: Approval[];
  finalState: RunState;
  script: ScriptStep[];
  playing: boolean;
  cancelled?: boolean;
}

type Listener = { onEvent: (e: RunEvent) => void; onStatus: (s: RunSummary) => void };
const listeners = new Map<string, Set<Listener>>();
const runs = new Map<string, MockRun>();

function summaryOf(d: RunDetail): RunSummary {
  const { id, requirement_id, title, provider, status, stage, outcome, error, created_at, updated_at, gate } = d;
  const stalled = (status === "created" || status === "running") && !runs.get(id)?.playing;
  return { id, requirement_id, title, provider, status, stage, outcome, error, created_at, updated_at, gate, stalled };
}

function emitStatus(run: MockRun) {
  const s = summaryOf(run.detail);
  listeners.get(run.detail.id)?.forEach((l) => l.onStatus(s));
}

function pushEvent(run: MockRun, kind: string, node: string | null, payload: Record<string, unknown> = {}, ts = Date.now()) {
  const ev: RunEvent = { id: run.events.length + 1, run_id: run.detail.id, ts: iso(ts), node, kind, payload };
  run.events.push(ev);
  run.detail.updated_at = ev.ts;
  listeners.get(run.detail.id)?.forEach((l) => l.onEvent(ev));
  return ev;
}

function addAudit(run: MockRun, actor: string, action: string, details: Record<string, unknown>, ts = Date.now()) {
  const prev = run.audit.length ? run.audit[run.audit.length - 1].hash : "0".repeat(64);
  const seq = run.audit.length + 1;
  const hash = fakeHash(`${prev}|${seq}|${actor}|${action}|${JSON.stringify(details)}`);
  run.audit.push({ seq, ts: iso(ts), actor, action, details, prev_hash: prev, hash });
}

function materialize(runId: string, base: number, steps: ScriptStep[]): RunEvent[] {
  return steps.map((s, i) => ({
    id: i + 1,
    run_id: runId,
    ts: iso(base + s.at * 1000),
    node: s.node,
    kind: s.kind,
    payload: clone(s.payload ?? {}),
  }));
}

function buildCalls(base: number, seeds: typeof req101CallSeeds): LLMCall[] {
  return seeds.map(({ at, ...rest }, i) => ({ ...rest, id: i + 1, ts: iso(base + at * 1000), cached: 0 }));
}

function fillAttemptTimes(state: RunState, base: number) {
  state.attempts_log?.forEach((a, i) => {
    a.ts = iso(base + (180 + i * 25) * 1000);
  });
}

function newRun(init: {
  id: string;
  base: number;
  requirementPath: string;
  provider: ProviderName;
  status: RunStatus;
  stage: string | null;
  gate: Gate | null;
  next: string[];
  state: RunState;
  finalState: RunState;
  script: ScriptStep[];
  events: RunEvent[];
  diff: DiffResponse;
  usage: UsageByAgent[];
  calls: LLMCall[];
  outcome?: string | null;
  error?: string | null;
}): MockRun {
  const req = requirements.find((r) => r.path === init.requirementPath) ?? requirements[0];
  const updated = init.events.length ? init.events[init.events.length - 1].ts : iso(init.base);
  return {
    detail: {
      id: init.id,
      requirement_id: req.id,
      title: req.title,
      provider: init.provider,
      status: init.status,
      stage: init.stage,
      outcome: init.outcome ?? null,
      error: init.error ?? null,
      created_at: iso(init.base),
      updated_at: updated,
      gate: init.gate,
      requirement: { id: req.id, title: req.title, body: req.body, path: req.path, priority: req.priority },
      state: init.state,
      next: init.next,
    },
    events: init.events,
    diff: init.diff,
    usageAgents: init.usage,
    calls: init.calls,
    audit: [],
    approvals: [],
    finalState: init.finalState,
    script: init.script,
    playing: false,
  };
}

// ------------------------------------------------------------------ REQ-102 follow-up fixtures (after a human decision)

function req102Evaluation(): Evaluation {
  return {
    golden: { available: true, passed: 8, total: 8, failures: [] },
    rubric: {
      items: [
        { criterion: "Requirement coverage", score: 5, justification: "Every refund writes exactly one paylite.audit record with the requested fields." },
        { criterion: "Correctness and edge cases", score: 4, justification: "Logging cannot block the refund; free-text reasons are logged verbatim." },
        { criterion: "Scope discipline", score: 5, justification: "Only app/refunds.py changed, as designed." },
        { criterion: "Code quality", score: 4, justification: "Structured extra= fields; message format could be simplified." },
        { criterion: "Security and compliance", score: 5, justification: "Only the masked card (last four digits) is logged." },
      ],
      overall_comment: "Matches the reference implementation after the human-guided retry.",
    },
    scores: { golden_pass_rate: 1, rubric_percent: 92, overall: 96.8 },
    reference_files: ["app/refunds.py"],
    agent_files: ["app/refunds.py"],
  };
}

function req102Release(override: boolean): ReleaseEvidence {
  return {
    headline: "Audit log entry for every refund",
    change_summary: "Each refund now writes one structured record on the paylite.audit logger for support investigations.",
    recommendation: override ? "request_changes" : "approve",
    recommendation_reason: override
      ? "Tests pass, but a high-severity finding (full card number in logs) was accepted by human override. Recommend fixing before release."
      : "All tests and 8/8 golden tests pass, the blocking finding was fixed by logging the masked card, and scope was respected.",
    criteria_evidence: [
      { criterion_id: "AC-1", status: "met", evidence: "test_audit_record_written_once" },
      { criterion_id: "AC-2", status: "met", evidence: "test_audit_record_fields" },
      { criterion_id: "AC-3", status: "met", evidence: "test_audit_record_identifies_card" },
      { criterion_id: "AC-4", status: "met", evidence: "test_refund_behaviour_unchanged" },
    ],
    known_risks: override
      ? ["Full card numbers will be present in log storage (PCI DSS scope expands).", "Free-text refund reasons may contain PII."]
      : ["Free-text refund reasons may contain PII."],
    rollback_plan: "Revert the merge commit; no data migration.",
    facts: {
      all_tests_passed: true,
      tests: "31 passed, 0 failed, 0 errors",
      coverage_percent: 97.9,
      lint_issues: 0,
      blocking_security_findings: 0,
      security_override: override,
      scope_verdict: "within the architect-approved files",
      developer_attempts: override ? 2 : 3,
      golden_tests_hidden_from_agents: 8,
    },
    diff_stat: { files: 1, added: 10, removed: 0 },
    branch: "forgeline/req-102-b81e4d07",
    commit: "51aa7d93c0e64b8f2d17e9a5c3b0f48e6d2c19a7",
  };
}

function approvalGateFrom(release: ReleaseEvidence, evaluation: Evaluation | undefined): ReleaseApprovalGate {
  return {
    gate: "release_approval",
    headline: release.headline,
    recommendation: release.recommendation as ReleaseApprovalGate["recommendation"],
    reason: release.recommendation_reason,
    branch: release.branch,
    commit: release.commit,
    facts: release.facts,
    scores: evaluation?.scores ?? null,
    known_risks: release.known_risks,
  };
}

function req102TailSteps(release: ReleaseEvidence, evaluation: Evaluation, withDeveloper: boolean): ScriptStep[] {
  const dev: ScriptStep[] = withDeveloper
    ? [
        { at: 0, node: "developer", kind: "node_start" },
        { at: 12, node: "developer", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-pro", latency_ms: 11800, input_tokens: 10980, output_tokens: 940, repairs: 0 } },
        { at: 13, node: "developer", kind: "node_end", payload: { duration_ms: 12600, summary: "attempt 3: 1 file(s), 1 edit(s)" } },
        { at: 13, node: "apply_change", kind: "node_start" },
        { at: 13, node: "apply_change", kind: "node_end", payload: { duration_ms: 70, summary: "wrote 1 file(s) within approved scope" } },
        { at: 13, node: "qa_run", kind: "node_start" },
        { at: 24, node: "qa_run", kind: "node_end", payload: { duration_ms: 10700, summary: "31 passed, 0 failed, 0 errors; coverage 97.9%; 0 lint issue(s)" } },
        { at: 24, node: "security", kind: "node_start" },
        { at: 32, node: "security", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 5600, input_tokens: 5120, output_tokens: 700, repairs: 0 } },
        { at: 33, node: "security", kind: "node_end", payload: { duration_ms: 8800, summary: "1 finding(s), 0 blocking" } },
      ]
    : [];
  return [
    ...dev,
    { at: 34, node: "evaluate", kind: "node_start" },
    { at: 46, node: "evaluator", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 7200, input_tokens: 7020, output_tokens: 990, repairs: 0 } },
    { at: 47, node: "evaluate", kind: "node_end", payload: { duration_ms: 12900, summary: `golden ${evaluation.golden.passed}/${evaluation.golden.total}; overall ${evaluation.scores.overall}` } },
    { at: 47, node: "release", kind: "node_start" },
    { at: 57, node: "release", kind: "llm_call", payload: { provider: "gemini", model: "gemini-2.5-flash", latency_ms: 8300, input_tokens: 6240, output_tokens: 1120, repairs: 0 } },
    { at: 58, node: "release", kind: "node_end", payload: { duration_ms: 10800, summary: `recommendation: ${release.recommendation}; commit ${release.commit.slice(0, 8)}` } },
    { at: 58, node: "approval", kind: "node_start" },
    { at: 58, node: null, kind: "gate_opened", payload: { gate: approvalGateFrom(release, evaluation) } },
  ];
}

// ------------------------------------------------------------------ seed runs

function seed() {
  const now = Date.now();

  // 1) REQ-101 awaiting release approval
  {
    const id = "run_7f3a9c21";
    const base = now - 14 * 60_000;
    const state = clone(req101State);
    fillAttemptTimes(state, base);
    const answeredAt = iso(base + 95_000);
    state.human_decisions = [{ gate: "clarification", actor: "Priya Sharma", ts: answeredAt, comment: "2 answers" }];
    const run = newRun({
      id, base, requirementPath: "requirements/partial_refund.md", provider: "gemini",
      status: "awaiting_human", stage: "approval", gate: clone(req101ApprovalGate), next: ["approval"],
      state, finalState: clone(state), script: req101Script, events: materialize(id, base, req101Script),
      diff: req101Diff, usage: req101UsageByAgent, calls: buildCalls(base, req101CallSeeds),
    });
    req101AuditSeeds.forEach(([at, actor, action, details]) => addAudit(run, actor, action, details, base + at * 1000));
    run.approvals.push({ ts: answeredAt, gate: "clarification", decision: "answered", actor: "Priya Sharma", comment: "Notify on every partial refund; reason stays optional." });
    runs.set(id, run);
  }

  // 2) REQ-102 escalated: security block
  {
    const id = "run_b81e4d07";
    const base = now - 52 * 60_000;
    const state = clone(req102State);
    fillAttemptTimes(state, base - 100_000);
    const calls: LLMCall[] = req102Script
      .filter((s) => s.kind === "llm_call")
      .map((s, i) => {
        const p = s.payload as Record<string, number | string>;
        return {
          id: i + 1, ts: iso(base + s.at * 1000), agent: s.node ?? "agent", provider: String(p.provider), model: String(p.model),
          tier: String(p.model).includes("pro") ? "code" : "light", input_tokens: Number(p.input_tokens), output_tokens: Number(p.output_tokens),
          latency_ms: Number(p.latency_ms), cost_usd: Number(((Number(p.input_tokens) * 1.25 + Number(p.output_tokens) * 10) / 1e6).toFixed(4)),
          cached: 0, attempt: 1, status: Number(p.repairs) > 0 ? "repaired" : "ok", error: null,
        } satisfies LLMCall;
      });
    const run = newRun({
      id, base, requirementPath: "requirements/refund_audit_log.md", provider: "gemini",
      status: "awaiting_human", stage: "escalate", gate: clone(req102EscalationGate), next: ["escalate"],
      state, finalState: clone(state), script: req102Script, events: materialize(id, base, req102Script),
      diff: req102Diff, usage: req102UsageByAgent, calls,
    });
    ([
      [0, "system", "run_started", { requirement: "REQ-102", provider: "gemini" }],
      [10, "agent:analyst", "analysis_produced", { criteria: 4, tasks: 2, questions: 0 }],
      [28, "agent:architect", "design_approved", { files: ["app/refunds.py"] }],
      [44, "agent:qa", "acceptance_tests_written", { tests: 6, failing_before_change: 4 }],
      [58, "agent:developer", "files_written", { files: ["app/refunds.py"], attempt: 1 }],
      [69, "agent:qa", "checks_run", { attempt: 1, passed: 31, failed: 0 }],
      [79, "agent:security", "blocking_finding", { rule: "card-number-logged", severity: "high", file: "app/refunds.py:41" }],
      [104, "agent:qa", "checks_run", { attempt: 2, passed: 31, failed: 0 }],
      [113, "system", "escalated", { reason: "security_block", node: "security" }],
    ] as [number, string, string, Record<string, unknown>][]).forEach(([at, a, act, d]) => addAudit(run, a, act, d, base + at * 1000));
    runs.set(id, run);
  }

  // 3) REQ-101 completed and merged yesterday
  {
    const id = "run_2c90e6f5";
    const base = now - 26 * 3_600_000;
    const tail: ScriptStep[] = [
      { at: 412, node: null, kind: "gate_answered", payload: { gate: "release_approval", decision: "approve", actor: "Rahul Mehta" } },
      { at: 412, node: "approval", kind: "node_start" },
      { at: 412, node: "approval", kind: "node_end", payload: { duration_ms: 44, summary: "approve by Rahul Mehta" } },
      { at: 413, node: "merge_deploy", kind: "node_start" },
      { at: 415, node: "merge_deploy", kind: "node_end", payload: { duration_ms: 2140, summary: "merged 5e2a91c0; deployed to staging" } },
      { at: 415, node: null, kind: "run_finished", payload: { outcome: "merged" } },
    ];
    const script = [...req101Script, ...tail];
    const state = clone(req101State);
    fillAttemptTimes(state, base);
    state.human_decisions = [
      { gate: "clarification", actor: "Priya Sharma", ts: iso(base + 95_000), comment: "2 answers" },
      { gate: "release_approval", decision: "approve", actor: "Rahul Mehta", comment: "Evidence is complete. Ship it.", ts: iso(base + 412_000) },
    ];
    state.outcome = "merged";
    const run = newRun({
      id, base, requirementPath: "requirements/partial_refund.md", provider: "gemini",
      status: "completed", stage: "merge_deploy", gate: null, next: [], outcome: "merged",
      state, finalState: clone(state), script, events: materialize(id, base, script),
      diff: req101Diff, usage: req101UsageByAgent, calls: buildCalls(base, req101CallSeeds),
    });
    req101AuditSeeds.forEach(([at, actor, action, details]) => addAudit(run, actor, action, details, base + at * 1000));
    addAudit(run, "human:Rahul Mehta", "release_decision", { decision: "approve", comment: "Evidence is complete. Ship it." }, base + 412_000);
    addAudit(run, "human:Rahul Mehta", "merged_to_main", { branch: "forgeline/req-101-2c90e6f5", merge_commit: "5e2a91c0" }, base + 414_000);
    addAudit(run, "system", "deployed", { environment: "staging", commit: "5e2a91c0", approved_by: "Rahul Mehta" }, base + 415_000);
    run.approvals.push(
      { ts: iso(base + 95_000), gate: "clarification", decision: "answered", actor: "Priya Sharma", comment: "Notify on every partial refund." },
      { ts: iso(base + 412_000), gate: "release_approval", decision: "approve", actor: "Rahul Mehta", comment: "Evidence is complete. Ship it." },
    );
    runs.set(id, run);
  }

  // 4) REQ-102 paused: all providers unavailable
  {
    const id = "run_e4d1a6b3";
    const base = now - 3 * 3_600_000;
    const err = "All LLM providers unavailable. gemini: 429 RESOURCE_EXHAUSTED (quota exceeded); ollama: connection refused (http://localhost:11434)";
    const steps: ScriptStep[] = [
      { at: 0, node: null, kind: "run_created", payload: { requirement: "REQ-102", provider: "gemini" } },
      { at: 1, node: "intake", kind: "node_start" },
      { at: 2, node: "intake", kind: "node_end", payload: { duration_ms: 1500, summary: "worktree forgeline/req-102-e4d1a6b3 @ 4e1b9d02" } },
      { at: 2, node: "analyst", kind: "node_start" },
      { at: 4, node: "analyst", kind: "llm_retry", payload: { provider: "gemini", delay_s: 2, error: "429 RESOURCE_EXHAUSTED" } },
      { at: 8, node: "analyst", kind: "llm_provider_failed", payload: { from: "gemini", to: "ollama", error: "429 RESOURCE_EXHAUSTED: quota exceeded" } },
      { at: 9, node: "analyst", kind: "llm_provider_failed", payload: { from: "ollama", to: null, error: "connection refused (http://localhost:11434)" } },
      { at: 9, node: null, kind: "run_paused", payload: { reason: "all LLM providers unavailable", error: err } },
    ];
    const finalState = clone(req102State);
    const run = newRun({
      id, base, requirementPath: "requirements/refund_audit_log.md", provider: "gemini",
      status: "paused_provider_unavailable", stage: "analyst", gate: null, next: ["analyst"], error: err,
      state: {}, finalState, script: req102Script, events: materialize(id, base, steps),
      diff: { diff: "", stat: { files: 0, added: 0, removed: 0 } }, usage: [], calls: [
        { id: 1, ts: iso(base + 4000), agent: "analyst", provider: "gemini", model: "gemini-2.5-flash", tier: "light", input_tokens: 0, output_tokens: 0, latency_ms: 420, cost_usd: 0, cached: 0, attempt: 1, status: "provider_error", error: "429 RESOURCE_EXHAUSTED" },
        { id: 2, ts: iso(base + 9000), agent: "analyst", provider: "ollama", model: "llama3.1:8b", tier: "light", input_tokens: 0, output_tokens: 0, latency_ms: 12, cost_usd: 0, cached: 0, attempt: 1, status: "provider_error", error: "connection refused" },
      ],
    });
    addAudit(run, "system", "run_started", { requirement: "REQ-102", provider: "gemini" }, base);
    addAudit(run, "system", "run_paused", { reason: "all LLM providers unavailable" }, base + 9000);
    runs.set(id, run);
  }

  // 5) REQ-102 stalled: marked running, but nothing is executing it
  {
    const id = "run_5a17c3de";
    const base = now - 40 * 60_000;
    const steps: ScriptStep[] = [
      { at: 0, node: null, kind: "run_created", payload: { requirement: "REQ-102", provider: "gemini" } },
      { at: 1, node: "intake", kind: "node_start" },
      { at: 2, node: "intake", kind: "node_end", payload: { duration_ms: 1500, summary: "worktree forgeline/req-102-5a17c3de @ 4e1b9d02" } },
      { at: 2, node: "analyst", kind: "node_start" },
    ];
    const run = newRun({
      id, base, requirementPath: "requirements/refund_audit_log.md", provider: "gemini",
      status: "running", stage: "analyst", gate: null, next: ["analyst"],
      state: {}, finalState: clone(req102State), script: req102Script, events: materialize(id, base, steps),
      diff: { diff: "", stat: { files: 0, added: 0, removed: 0 } }, usage: [], calls: [],
    });
    addAudit(run, "system", "run_started", { requirement: "REQ-102", provider: "gemini" }, base);
    runs.set(id, run);
  }
}

seed();

// ------------------------------------------------------------------ replay engine

const GATE_NODE: Record<string, string> = { clarification: "clarify", release_approval: "approval", escalation: "escalate" };

function applyNodeEnd(run: MockRun, node: string, summary: string) {
  const s = run.detail.state;
  const f = run.finalState;
  const ends = (n: string) => run.events.filter((e) => e.kind === "node_end" && e.node === n);
  const attemptsSoFar =
    ends("qa_run").length + ends("apply_change").filter((e) => String(e.payload.summary ?? "").includes("rejected")).length;
  switch (node) {
    case "analyst":
      s.analysis = clone(f.analysis);
      break;
    case "clarify":
      s.clarifications = clone(f.clarifications);
      break;
    case "architect":
      s.design = clone(f.design);
      s.impact_warnings = clone(f.impact_warnings);
      break;
    case "qa_author":
      s.acceptance = clone(f.acceptance);
      break;
    case "developer":
      s.dev_attempt = (s.dev_attempt ?? 0) + 1;
      s.dev_budget = Math.max(s.dev_budget ?? 0, f.dev_budget ?? 3);
      break;
    case "apply_change": {
      if (summary.includes("rejected")) {
        const v = [...run.events].reverse().find((e) => e.kind === "scope_violation");
        s.scope = { ok: false, violations: (v?.payload.violations as ScopeViolation[] | undefined) ?? [] };
      } else {
        s.scope = clone(f.scope);
      }
      s.attempts_log = clone(f.attempts_log?.slice(0, attemptsSoFar));
      s.attempts_log?.forEach((a) => (a.ts = a.ts || iso(Date.now())));
      break;
    }
    case "qa_run": {
      const failing = !/ 0 failed/.test(summary);
      s.qa = clone(f.qa);
      if (s.qa && failing) {
        const num = (re: RegExp) => Number(summary.match(re)?.[1] ?? 0);
        const failed = num(/(\d+) failed/);
        const failures = (f.feedback ?? [])
          .map((line) => line.match(/^TEST FAILED (\S+): (.*)$/))
          .filter((m): m is RegExpMatchArray => !!m)
          .slice(0, failed);
        s.qa.ok = false;
        s.qa.tests = {
          ...s.qa.tests,
          ok: false,
          passed: num(/(\d+) passed/),
          failed,
          errors: num(/(\d+) error/),
          total: num(/(\d+) passed/) + failed,
          coverage_percent: Number(summary.match(/coverage ([\d.]+)%/)?.[1] ?? s.qa.tests.coverage_percent),
          cases: s.qa.tests.cases.map((c) => {
            const hit = failures.find((m) => m[1] === c.nodeid);
            return hit ? { ...c, outcome: "failed", message: hit[2] } : c;
          }),
        };
      }
      s.feedback = failing ? clone(f.feedback) : [];
      s.attempts_log = clone(f.attempts_log?.slice(0, attemptsSoFar));
      s.attempts_log?.forEach((a) => (a.ts = a.ts || iso(Date.now())));
      break;
    }
    case "security":
      s.security = clone(f.security);
      s.security_fix_attempts = f.security_fix_attempts;
      break;
    case "evaluate":
      s.evaluation = clone(f.evaluation);
      break;
    case "release":
      s.release = clone(f.release);
      break;
  }
}

function play(run: MockRun, steps: ScriptStep[]) {
  run.playing = true;
  let i = 0;
  const tick = () => {
    if (run.cancelled) {
      run.playing = false;
      return;
    }
    if (i >= steps.length) {
      run.playing = false;
      return;
    }
    const step = steps[i];
    const payload = clone(step.payload ?? {});
    const d = run.detail;
    if (step.kind === "node_start" && step.node) {
      d.status = "running";
      d.stage = step.node;
      d.gate = null;
      d.next = [step.node];
    }
    pushEvent(run, step.kind, step.node, payload);
    if (step.kind === "node_end" && step.node) applyNodeEnd(run, step.node, String(payload.summary ?? ""));
    if (step.kind === "gate_opened") {
      const gate = payload.gate as Gate;
      d.status = "awaiting_human";
      d.gate = gate;
      d.stage = GATE_NODE[gate.gate] ?? d.stage;
      d.next = [GATE_NODE[gate.gate] ?? "approval"];
    }
    if (step.kind === "run_finished") {
      const outcome = String(payload.outcome);
      d.outcome = outcome;
      d.status = outcome === "merged" ? "completed" : (outcome as RunStatus);
      d.state.outcome = outcome as RunState["outcome"];
      d.gate = null;
      d.next = [];
    }
    if (["node_start", "gate_opened", "run_finished"].includes(step.kind)) emitStatus(run);
    i += 1;
    if (step.kind === "gate_opened") {
      run.playing = false;
      return;
    }
    const nextStep = steps[i];
    const gap = nextStep
      ? nextStep.gap ?? (step.gap != null ? 450 : Math.min(1400, Math.max(260, (nextStep.at - step.at) * 110)))
      : 0;
    setTimeout(tick, gap);
  };
  setTimeout(tick, 350);
}

// ------------------------------------------------------------------ resume

function conflict(detail: string): never {
  throw new ApiError(409, detail);
}

function resume(run: MockRun, body: ResumeBody): RunSummary {
  const d = run.detail;
  if (run.playing) conflict("Run is already executing");
  if (["completed", "rejected", "aborted"].includes(d.status)) conflict(`Run already finished (${d.status})`);
  const decision = body.decision as Record<string, unknown> | null;
  const actor = String(decision?.actor ?? "").trim() || "human";
  const comment = String(decision?.comment ?? "").trim();

  if (d.status === "paused_provider_unavailable" || d.status === "failed") {
    if (decision) conflict("This run is not waiting for a decision; resume with decision null");
    if (body.provider && body.provider !== d.provider) {
      d.provider = body.provider;
      pushEvent(run, "provider_switched", null, { provider: body.provider });
    }
    d.error = null;
    d.status = "running";
    run.playing = true;
    emitStatus(run);
    const startIdx = run.script.findIndex((s) => s.kind === "node_start" && s.node === d.stage);
    play(run, run.script.slice(Math.max(1, startIdx)));
    return summaryOf(d);
  }

  if (d.status !== "awaiting_human" || !d.gate) conflict("Run is not waiting for a human");
  if (!decision) conflict(`The ${d.gate.gate} gate needs a decision`);
  const gate = d.gate;
  const ts = iso(Date.now());

  if (gate.gate === "clarification") {
    const answers = Array.isArray(decision.answers) ? (decision.answers as unknown[]).map((a) => String(a).trim()) : [];
    if (answers.length !== gate.questions.length || answers.some((a) => !a))
      conflict(`Expected ${gate.questions.length} non-empty answer(s), one per question`);
    run.finalState.clarifications = gate.questions.map((q, i) => ({ question: q, answer: answers[i] }));
    d.state.human_decisions = [...(d.state.human_decisions ?? []), { gate: "clarification", actor, ts, comment: `${answers.length} answers` }];
    run.approvals.push({ ts, gate: "clarification", decision: "answered", actor, comment: answers.join(" | ") });
    addAudit(run, `human:${actor}`, "clarification_answered", { answers: answers.length });
    d.gate = null;
    d.status = "running";
    run.playing = true;
    emitStatus(run);
    const resumeAt = run.script.findIndex((s) => s.kind === "gate_answered" && s.payload?.gate === "clarification");
    const rest = run.script.slice(Math.max(0, resumeAt)).map((s) =>
      s.kind === "gate_answered" ? { ...s, payload: { ...s.payload, actor } } :
      s.kind === "node_end" && s.node === "clarify" ? { ...s, payload: { ...s.payload, summary: `${answers.length} answer(s) from ${actor}` } } : s,
    );
    play(run, rest);
    return summaryOf(d);
  }

  if (gate.gate === "release_approval") {
    const choice = String(decision.decision ?? "");
    if (!["approve", "request_changes", "reject"].includes(choice)) conflict(`Invalid release decision '${choice}'`);
    if (choice === "request_changes" && !comment) conflict("request_changes requires a comment describing the changes");
    d.state.human_decisions = [...(d.state.human_decisions ?? []), { gate: "release_approval", decision: choice, actor, comment, ts }];
    run.approvals.push({ ts, gate: "release_approval", decision: choice, actor, comment });
    addAudit(run, `human:${actor}`, "release_decision", { decision: choice, comment });
    d.gate = null;
    d.status = "running";
    run.playing = true;
    emitStatus(run);
    const head: ScriptStep[] = [
      { at: 0, node: null, kind: "gate_answered", payload: { gate: "release_approval", decision: choice, actor } },
      { at: 1, node: "approval", kind: "node_start" },
      { at: 1, node: "approval", kind: "node_end", payload: { duration_ms: 41, summary: `${choice} by ${actor}` } },
    ];
    if (choice === "approve") {
      addAudit(run, `human:${actor}`, "merged_to_main", { branch: d.state.release?.branch, merge_commit: "a71c3e09" });
      addAudit(run, "system", "deployed", { environment: "staging", approved_by: actor });
      play(run, [
        ...head,
        { at: 2, node: "merge_deploy", kind: "node_start" },
        { at: 6, node: "merge_deploy", kind: "node_end", payload: { duration_ms: 2310, summary: "merged a71c3e09; deployed to staging" } },
        { at: 7, node: null, kind: "run_finished", payload: { outcome: "merged" } },
      ]);
    } else if (choice === "reject") {
      addAudit(run, "system", "run_closed", { outcome: "rejected" });
      play(run, [
        ...head,
        { at: 2, node: "close", kind: "node_start" },
        { at: 3, node: "close", kind: "node_end", payload: { duration_ms: 12, summary: "rejected" } },
        { at: 4, node: null, kind: "run_finished", payload: { outcome: "rejected" } },
      ]);
    } else {
      run.finalState.feedback = [`HUMAN REVIEWER requested changes: ${comment}`];
      const lastDev = run.script.map((s) => s.kind === "node_start" && s.node === "developer").lastIndexOf(true);
      play(run, [...head, ...run.script.slice(lastDev >= 0 ? lastDev : 0).filter((s) => s.kind !== "run_finished")]);
    }
    return summaryOf(d);
  }

  // escalation
  const choice = String(decision.decision ?? "") as "retry" | "override" | "abort";
  if (!gate.options.includes(choice)) conflict(`'${choice}' is not an option for this escalation (allowed: ${gate.options.join(", ")})`);
  if (choice === "override" && !comment) conflict("An override requires a written justification in the comment");
  d.state.human_decisions = [...(d.state.human_decisions ?? []), { gate: "escalation", reason: gate.reason, decision: choice, actor, comment, ts }];
  run.approvals.push({ ts, gate: `escalation:${gate.reason}`, decision: choice, actor, comment });
  addAudit(run, `human:${actor}`, "escalation_decision", { reason: gate.reason, decision: choice, comment });
  d.gate = null;
  d.status = "running";
  run.playing = true;
  emitStatus(run);
  const head: ScriptStep[] = [
    { at: 0, node: null, kind: "gate_answered", payload: { gate: "escalation", decision: choice, actor } },
    { at: 1, node: "escalate", kind: "node_start" },
    { at: 1, node: "escalate", kind: "node_end", payload: { duration_ms: 35, summary: `${choice} by ${actor}` } },
  ];
  if (choice === "abort") {
    play(run, [
      ...head,
      { at: 2, node: "close", kind: "node_start" },
      { at: 3, node: "close", kind: "node_end", payload: { duration_ms: 10, summary: "aborted" } },
      { at: 4, node: null, kind: "run_finished", payload: { outcome: "aborted" } },
    ]);
    return summaryOf(d);
  }
  const override = choice === "override";
  const evaluation = req102Evaluation();
  if (override) {
    evaluation.scores = { golden_pass_rate: 1, rubric_percent: 72, overall: 85.6 };
    evaluation.rubric!.items[4] = { criterion: "Security and compliance", score: 1, justification: "Full card number is logged; accepted only by human override." };
    d.state.security_override = { actor, justification: comment, ts };
  }
  const release = req102Release(override);
  const f = run.finalState;
  f.evaluation = evaluation;
  f.release = release;
  if (!override && f.security) {
    f.security = { ...f.security, findings: f.security.findings.filter((x) => !x.introduced), blocking: [], introduced_count: 0 };
    f.attempts_log = [...(f.attempts_log ?? []), { attempt: 3, ts: "", stage: "qa", scope_ok: true, passed: 31, failed: 0, errors: 0, coverage: 97.9, lint_issues: 0 }];
  }
  play(run, [...head, ...req102TailSteps(release, evaluation, !override)]);
  return summaryOf(d);
}

// ------------------------------------------------------------------ cancel

function cancel(run: MockRun, body: unknown): RunSummary {
  const d = run.detail;
  if (["completed", "rejected", "aborted"].includes(d.status)) conflict(`Run already finished (${d.status})`);
  const actor = String((body as { actor?: string } | undefined)?.actor ?? "").trim() || "human";
  run.cancelled = true;
  run.playing = false;
  d.status = "aborted";
  d.outcome = "cancelled";
  d.state.outcome = "aborted";
  d.gate = null;
  d.next = [];
  d.error = null;
  pushEvent(run, "run_cancelled", null, { actor });
  pushEvent(run, "run_finished", null, { outcome: "cancelled" });
  addAudit(run, `human:${actor}`, "run_cancelled", { stage: d.stage });
  emitStatus(run);
  return summaryOf(d);
}

// ------------------------------------------------------------------ create

let adhocSeq = 200;

function titleFrom(text: string): string {
  const first = text.split("\n").map((l) => l.replace(/^#+\s*/, "").trim()).find(Boolean) ?? "Requirement";
  return first.length > 80 ? `${first.slice(0, 77)}…` : first;
}

function createTextRun(body: CreateRunBody & { requirement_text: string }): RunSummary {
  const text = body.requirement_text.trim();
  if (!text) throw new ApiError(422, "requirement_text must not be empty");
  const id = `run_${Math.random().toString(16).slice(2, 10)}`;
  const reqId = `REQ-${++adhocSeq}`;
  const title = body.title?.trim() || titleFrom(text);
  const fx = buildConsoleRun(reqId, title, body.provider, id);
  const run = newRun({
    id, base: Date.now(), requirementPath: requirements[0].path, provider: body.provider, status: "created", stage: null, gate: null, next: ["intake"],
    state: {}, finalState: fx.finalState, script: fx.script, events: [], diff: req101Diff, usage: req101UsageByAgent, calls: buildCalls(Date.now(), req101CallSeeds),
  });
  run.detail.requirement_id = reqId;
  run.detail.title = title;
  run.detail.requirement = { id: reqId, title, body: text, path: "", priority: "medium" };
  addAudit(run, "system", "run_started", { requirement: reqId, provider: body.provider, cache: body.cache, policy_sha256: policy.sha256.slice(0, 12) + "…" });
  runs.set(id, run);
  play(run, fx.script);
  return summaryOf(run.detail);
}

function createRun(body: CreateRunBody): RunSummary {
  if (typeof body.requirement_text === "string") return createTextRun(body as CreateRunBody & { requirement_text: string });
  const req = requirements.find((r) => r.path === body.requirement_path);
  if (!req) throw new ApiError(404, `Unknown requirement ${body.requirement_path}`);
  const id = `run_${Math.random().toString(16).slice(2, 10)}`;
  const base = Date.now();
  const isRefund = req.id === "REQ-101";
  const finalState = clone(isRefund ? req101State : req102State);
  finalState.human_decisions = [];
  const script = clone(isRefund ? req101Script : req102Script).map((s) =>
    s.kind === "run_created" ? { ...s, payload: { requirement: req.id, provider: body.provider } } :
    s.kind === "llm_call" && body.provider === "ollama" ? { ...s, payload: { ...s.payload, provider: "ollama", model: "qwen2.5-coder:14b" } } : s,
  );
  const run = newRun({
    id, base, requirementPath: req.path, provider: body.provider, status: "created", stage: null, gate: null, next: ["intake"],
    state: {}, finalState, script, events: [], diff: isRefund ? req101Diff : req102Diff,
    usage: isRefund ? req101UsageByAgent : req102UsageByAgent, calls: isRefund ? buildCalls(base, req101CallSeeds) : [],
  });
  addAudit(run, "system", "run_started", { requirement: req.id, provider: body.provider, cache: body.cache, policy_sha256: policy.sha256.slice(0, 12) + "…" });
  runs.set(id, run);
  play(run, script);
  return summaryOf(run.detail);
}

// ------------------------------------------------------------------ router

function usageOf(run: MockRun): UsageResponse {
  const keys: (keyof UsageNumbers)[] = ["calls", "input_tokens", "output_tokens", "latency_ms", "cost_usd", "cache_hits", "failures"];
  const totals: Partial<UsageNumbers> = {};
  keys.forEach((k) => {
    totals[k] = Number(run.usageAgents.reduce((acc, r) => acc + r[k], 0).toFixed(k === "cost_usd" ? 4 : 0));
  });
  return { by_agent: clone(run.usageAgents), totals, calls: clone(run.calls) };
}

function getRun(id: string): MockRun {
  const run = runs.get(id);
  if (!run) throw new ApiError(404, `Run ${id} not found`);
  return run;
}

export async function mockRequest<T>(method: string, fullPath: string, body?: unknown): Promise<T> {
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 160));
  const [path, query = ""] = fullPath.split("?");
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  const out = (v: unknown) => clone(v) as T;

  if (method === "GET" && path === "/health") return out({ status: "ok", version: "0.1.0" });
  if (method === "GET" && path === "/providers") return out(providers);
  if (method === "GET" && path === "/policy") return out(policy);
  if (method === "GET" && path === "/requirements") return out(requirements);
  if (method === "GET" && path === "/graph") return out(graph);
  if (path === "/runs") {
    if (method === "POST") return out(createRun(body as CreateRunBody));
    const list = [...runs.values()].map((r) => summaryOf(r.detail)).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return out(list);
  }
  if (parts[0] === "runs" && parts[1]) {
    const run = getRun(decodeURIComponent(parts[1]));
    const sub = parts[2];
    if (!sub && method === "GET") return out(run.detail);
    if (sub === "events") {
      const after = Number(params.get("after") ?? 0);
      return out(run.events.filter((e) => e.id > after));
    }
    if (sub === "resume" && method === "POST") return out(resume(run, body as ResumeBody));
    if (sub === "cancel" && method === "POST") return out(cancel(run, body));
    if (sub === "diff") return out(run.diff);
    if (sub === "usage") return out(usageOf(run));
    if (sub === "audit") return out({ entries: run.audit, verification: { ok: true, entries: run.audit.length, broken_at_seq: null, reason: "" } });
    if (sub === "approvals") return out(run.approvals);
  }
  throw new ApiError(404, `Not found: ${method} ${fullPath}`);
}

/** Mock replacement for the SSE stream. */
export function subscribe(
  runId: string,
  after: number,
  onEvent: (e: RunEvent) => void,
  onStatus: (s: RunSummary) => void,
): () => void {
  const run = runs.get(runId);
  if (!run) return () => undefined;
  const l: Listener = { onEvent, onStatus };
  if (!listeners.has(runId)) listeners.set(runId, new Set());
  listeners.get(runId)!.add(l);
  setTimeout(() => run.events.filter((e) => e.id > after).forEach((e) => onEvent(clone(e))), 0);
  return () => listeners.get(runId)?.delete(l);
}
