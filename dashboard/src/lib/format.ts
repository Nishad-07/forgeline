// Formatting helpers and human-readable labels.

import type { EscalationReason, RunStatus } from "../types";

export function parseTs(ts: string | null | undefined): number {
  if (!ts) return NaN;
  // Treat timezone-less timestamps as UTC (common for Python isoformat()).
  const hasTz = /([zZ]|[+-]\d\d:?\d\d)$/.test(ts);
  return Date.parse(hasTz ? ts : `${ts}Z`);
}

export function relativeTime(ts: string | null | undefined, now = Date.now()): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return "—";
  const diff = Math.round((now - t) / 1000);
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? "ago" : "from now";
  if (abs < 10) return "just now";
  if (abs < 60) return `${abs}s ${suffix}`;
  if (abs < 3600) return `${Math.floor(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.floor(abs / 3600)}h ${suffix}`;
  if (abs < 86400 * 7) return `${Math.floor(abs / 86400)}d ${suffix}`;
  return new Date(t).toLocaleDateString();
}

export function absoluteTime(ts: string | null | undefined): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleString();
}

export function clockTime(ts: string | null | undefined): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  if (m < 60) return `${m}m ${rem.toString().padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${(m % 60).toString().padStart(2, "0")}m`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US");
}

export function compactNumber(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 10_000) return `${Math.round(n / 1000)}k`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null || Number.isNaN(usd)) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(3)}`;
}

export function formatPercent(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

export function shortHash(h: string | null | undefined, n = 8): string {
  return h ? h.slice(0, n) : "—";
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  const t = s.replace(/[_:-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export const STATUS_LABEL: Record<RunStatus, string> = {
  created: "Created",
  running: "Running",
  awaiting_human: "Needs your input",
  paused_provider_unavailable: "Paused",
  failed: "Failed",
  completed: "Completed",
  rejected: "Rejected",
  aborted: "Aborted",
};

/** Statuses where the run is over. */
export const FINISHED_STATUSES: ReadonlySet<string> = new Set(["completed", "rejected", "aborted"]);

export function isFinished(status: string): boolean {
  return FINISHED_STATUSES.has(status);
}

/** A run that was stopped by a person (aborted with outcome "cancelled"). */
export function isStopped(run: { status: string; outcome?: string | null }): boolean {
  return run.status === "aborted" && run.outcome === "cancelled";
}

export const ESCALATION_LABEL: Record<EscalationReason, string> = {
  malformed_output: "Agent kept producing unusable output",
  retry_budget_exhausted: "Retry budget exhausted: tests still failing",
  security_block: "Blocking security finding",
  design_deadlock: "Architect and developer deadlocked",
};

export const GATE_LABEL: Record<string, string> = {
  clarification: "Clarification needed",
  release_approval: "Release approval",
  escalation: "Needs your decision",
};

export const RECOMMENDATION_LABEL: Record<string, string> = {
  approve: "Approve",
  request_changes: "Request changes",
  reject: "Reject",
};

export const NODE_LABEL: Record<string, string> = {
  intake: "Intake",
  analyst: "Analyst",
  clarify: "Questions",
  architect: "Architect",
  reconcile: "Reconcile",
  qa_author: "QA tests",
  developer: "Developer",
  apply_change: "Change check",
  qa_run: "Checks",
  security: "Security",
  evaluate: "Evaluation",
  evaluator: "Evaluation",
  release: "Release",
  approval: "Release approval",
  merge_deploy: "Merge",
  escalate: "Needs your decision",
  close: "Close",
  qa: "QA",
};

export function nodeLabel(id: string | null | undefined): string {
  if (!id) return "";
  return NODE_LABEL[id] ?? humanize(id);
}

export const FACT_LABEL: Record<string, string> = {
  all_tests_passed: "All tests passed",
  tests: "Tests",
  coverage_percent: "Coverage",
  lint_issues: "Lint issues",
  blocking_security_findings: "Blocking security findings",
  security_override: "Security override",
  scope_verdict: "Scope verdict",
  developer_attempts: "Developer attempts",
  golden_tests_hidden_from_agents: "Hidden golden tests passed",
};

export function formatFact(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "coverage_percent" && typeof value === "number") return `${value}%`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
