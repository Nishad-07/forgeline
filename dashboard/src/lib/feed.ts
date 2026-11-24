// Builds the Agent Console feed (one card per node execution, inline gates and notices)
// from the event log, the live LLM streams and the run detail.

import type { LiveCall } from "../hooks/useRunStream";
import type { Gate, RunDetail, RunEvent, ScopeViolation } from "../types";

/** Agent name (llm_* / agent_output events) -> workflow node id. */
export const AGENT_NODE: Record<string, string> = {
  analyst: "analyst",
  architect: "architect",
  qa: "qa_author",
  developer: "developer",
  security: "security",
  evaluator: "evaluate",
  release: "release",
};

export const NODE_AGENT: Record<string, string> = Object.fromEntries(Object.entries(AGENT_NODE).map(([a, n]) => [n, a]));

/** Nodes represented by gate items (or not shown at all) rather than cards. */
const NO_CARD = new Set(["intake", "clarify", "approval", "escalate", "reconcile"]);

const NOTICE_KINDS = new Set([
  "llm_retry",
  "llm_repair",
  "llm_failover",
  "llm_provider_failed",
  "agent_output_rejected",
  "agent_escalation",
  "scope_violation",
  "repeated_action_blocked",
  "duplicate_action_skipped",
  "qa_tests_rejected",
  "triage_unavailable",
  "rubric_unavailable",
  "provider_switched",
]);

export type CardStatus = "running" | "done" | "failed" | "paused";

export interface FeedCard {
  type: "card";
  key: string;
  node: string;
  /** agent name for LLM nodes */
  agent?: string;
  attempt: number;
  status: CardStatus;
  startId: number;
  startTs?: string;
  summary?: string;
  durationMs?: number;
  error?: string;
  /** live LLM call ids attached to this execution, in order */
  calls: string[];
  output?: unknown;
  hasOutput: boolean;
  notices: RunEvent[];
  llm: RunEvent[];
  violations: ScopeViolation[];
  /** latest execution of this node (run.state reflects it) */
  isLatest: boolean;
}

export interface FeedGate {
  type: "gate";
  key: string;
  gate: Gate;
  opened?: RunEvent;
  answered?: RunEvent;
  /** the run is currently waiting on this gate */
  active: boolean;
}

export interface FeedNotice {
  type: "notice";
  key: string;
  event: RunEvent;
}

export interface FeedHalt {
  type: "halt";
  key: string;
  event?: RunEvent;
  kind: "paused" | "failed";
  active: boolean;
}

export interface FeedOutcome {
  type: "outcome";
  key: string;
  event: RunEvent;
}

export type FeedItem = FeedCard | FeedGate | FeedNotice | FeedHalt | FeedOutcome;

export function nodeOfEvent(e: RunEvent): string | null {
  if (e.kind === "agent_output") {
    const a = e.payload?.agent;
    if (typeof a === "string") return AGENT_NODE[a] ?? a;
  }
  if (!e.node) return null;
  return AGENT_NODE[e.node] ?? e.node;
}

type Item = { id: number; ev?: RunEvent; call?: LiveCall };

export function buildFeed(events: RunEvent[], calls: LiveCall[], run: RunDetail | undefined): FeedItem[] {
  const items: Item[] = [...events.map((ev) => ({ id: ev.id, ev })), ...calls.map((call) => ({ id: call.firstId, call }))];
  items.sort((a, b) => a.id - b.id || (a.ev ? -1 : 1));

  const feed: FeedItem[] = [];
  const open = new Map<string, FeedCard>();
  const last = new Map<string, FeedCard>();
  const attempts = new Map<string, number>();

  const newCard = (node: string, id: number, ts?: string): FeedCard => {
    const attempt = (attempts.get(node) ?? 0) + 1;
    attempts.set(node, attempt);
    const card: FeedCard = {
      type: "card",
      key: `card-${node}-${attempt}`,
      node,
      agent: NODE_AGENT[node],
      attempt,
      status: "running",
      startId: id,
      startTs: ts,
      calls: [],
      hasOutput: false,
      notices: [],
      llm: [],
      violations: [],
      isLatest: false,
    };
    feed.push(card);
    open.set(node, card);
    last.set(node, card);
    return card;
  };

  const target = (node: string, id: number, ts?: string): FeedCard => open.get(node) ?? last.get(node) ?? newCard(node, id, ts);

  for (const it of items) {
    if (it.call) {
      const node = AGENT_NODE[it.call.agent] ?? it.call.agent;
      if (!node) continue;
      const card = open.get(node) ?? last.get(node) ?? newCard(node, it.id);
      if (!card.calls.includes(it.call.call)) card.calls.push(it.call.call);
      continue;
    }
    const e = it.ev!;
    const node = nodeOfEvent(e);
    switch (e.kind) {
      case "node_start": {
        if (!node || NO_CARD.has(node)) break;
        const cur = open.get(node);
        if (cur && (cur.status === "running" || cur.status === "paused")) {
          cur.status = "running";
          break;
        }
        newCard(node, e.id, e.ts);
        break;
      }
      case "node_end": {
        if (!node || NO_CARD.has(node)) break;
        const card = open.get(node) ?? newCard(node, e.id, e.ts);
        card.status = "done";
        if (typeof e.payload.summary === "string") card.summary = e.payload.summary;
        if (typeof e.payload.duration_ms === "number") card.durationMs = e.payload.duration_ms;
        open.delete(node);
        break;
      }
      case "node_error": {
        if (!node || NO_CARD.has(node)) break;
        const card = target(node, e.id, e.ts);
        card.status = "failed";
        card.error = String(e.payload.error ?? "");
        open.delete(node);
        break;
      }
      case "agent_output": {
        if (!node) break;
        const card = target(node, e.id, e.ts);
        card.output = e.payload.output;
        card.hasOutput = e.payload.output != null;
        break;
      }
      case "llm_call": {
        if (!node) break;
        target(node, e.id, e.ts).llm.push(e);
        break;
      }
      case "gate_opened": {
        const gate = e.payload.gate as Gate | undefined;
        if (gate) feed.push({ type: "gate", key: `gate-${e.id}`, gate, opened: e, active: false });
        break;
      }
      case "gate_answered": {
        const g = String(e.payload.gate ?? "");
        for (let i = feed.length - 1; i >= 0; i--) {
          const f = feed[i];
          if (f.type === "gate" && !f.answered && f.gate.gate === g) {
            f.answered = e;
            break;
          }
        }
        break;
      }
      case "run_paused":
      case "run_failed": {
        const kind = e.kind === "run_paused" ? "paused" : "failed";
        open.forEach((c) => {
          if (c.status === "running") c.status = kind === "paused" ? "paused" : "failed";
        });
        feed.push({ type: "halt", key: `halt-${e.id}`, event: e, kind, active: false });
        break;
      }
      case "run_finished":
        feed.push({ type: "outcome", key: `outcome-${e.id}`, event: e });
        break;
      default:
        if (NOTICE_KINDS.has(e.kind)) {
          const card = node ? open.get(node) : undefined;
          if (card) {
            card.notices.push(e);
            if (e.kind === "scope_violation" && Array.isArray(e.payload.violations)) card.violations.push(...(e.payload.violations as ScopeViolation[]));
          } else {
            feed.push({ type: "notice", key: `notice-${e.id}`, event: e });
          }
        }
        break;
    }
  }

  last.forEach((c) => (c.isLatest = true));

  if (run) {
    const terminal = ["completed", "rejected", "aborted"].includes(run.status);
    if (terminal) open.forEach((c) => c.status === "running" && (c.status = "done"));

    if (run.status === "awaiting_human" && run.gate) {
      let found = false;
      for (let i = feed.length - 1; i >= 0; i--) {
        const f = feed[i];
        if (f.type === "gate" && !f.answered && f.gate.gate === run.gate.gate) {
          f.active = true;
          f.gate = run.gate;
          found = true;
          break;
        }
      }
      if (!found) feed.push({ type: "gate", key: "gate-current", gate: run.gate, active: true });
    }

    if (run.status === "paused_provider_unavailable" || run.status === "failed") {
      const kind = run.status === "failed" ? "failed" : "paused";
      let found = false;
      for (let i = feed.length - 1; i >= 0; i--) {
        const f = feed[i];
        if (f.type === "card" && f.status === "done") break;
        if (f.type === "halt") {
          f.active = true;
          found = true;
          break;
        }
      }
      if (!found) feed.push({ type: "halt", key: "halt-current", kind, active: true });
      open.forEach((c) => c.status === "running" && (c.status = kind === "paused" ? "paused" : "failed"));
    }
  }

  return feed;
}

// ------------------------------------------------------------------ summary parsing

export interface TestSummary {
  passed?: number;
  failed?: number;
  errors?: number;
  coverage?: number;
  lint?: number;
}

export function parseTestSummary(s: string | undefined): TestSummary {
  if (!s) return {};
  const num = (re: RegExp) => {
    const m = s.match(re);
    return m ? Number(m[1]) : undefined;
  };
  return {
    passed: num(/(\d+)\s+passed/),
    failed: num(/(\d+)\s+failed/),
    errors: num(/(\d+)\s+error/),
    coverage: num(/coverage\s+([\d.]+)%/),
    lint: num(/(\d+)\s+lint/),
  };
}

export function parseEvalSummary(s: string | undefined): { passed?: number; total?: number; overall?: number } {
  if (!s) return {};
  const g = s.match(/golden\s+(\d+)\/(\d+)/);
  const o = s.match(/overall\s+([\d.]+)/);
  return { passed: g ? Number(g[1]) : undefined, total: g ? Number(g[2]) : undefined, overall: o ? Number(o[1]) : undefined };
}

// ------------------------------------------------------------------ stepper

export const STAGES: { id: string; label: string; nodes: string[] }[] = [
  { id: "plan", label: "Plan", nodes: ["analyst", "clarify"] },
  { id: "design", label: "Design", nodes: ["architect"] },
  { id: "tests", label: "Tests", nodes: ["qa_author"] },
  { id: "code", label: "Code", nodes: ["developer", "apply_change"] },
  { id: "checks", label: "Checks", nodes: ["qa_run", "security"] },
  { id: "review", label: "Review", nodes: ["evaluate", "release"] },
  { id: "approval", label: "Approval", nodes: ["approval", "merge_deploy"] },
];
