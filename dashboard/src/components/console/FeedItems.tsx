import { ChevronDown, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { LiveCall } from "../../hooks/useRunStream";
import { parseEvalSummary, parseTestSummary, type FeedCard, type FeedGate, type FeedHalt, type FeedOutcome } from "../../lib/feed";
import { humanize, nodeLabel } from "../../lib/format";
import type { Evaluation, QAResult, RunDetail, RunEvent, RunState, RunSummary, Scope, ScopeViolation, SecurityResult } from "../../types";
import { GatePanel, OutcomeBanner } from "../run/gates/GatePanel";
import { toPercent } from "../ui/Gauge";
import { cx } from "../ui/primitives";
import { StreamView } from "./CodeBlock";
import { AnalystOutput, ArchitectOutput, DeveloperOutput, QAOutput, ReleaseOutput, RubricOutput, Section, SecurityOutput } from "./outputs";

/** Plain-language card titles. */
const TITLE: Record<string, string> = {
  analyst: "Analyst",
  architect: "Architect",
  qa_author: "QA tests",
  developer: "Developer",
  apply_change: "Change check",
  qa_run: "Checks",
  security: "Security",
  evaluate: "Evaluation",
  release: "Release",
  merge_deploy: "Merge",
  close: "Closed",
};

/** Run-state slices captured while a card was the latest execution of its node. */
export interface CardSnapshot {
  qa?: QAResult;
  scope?: Scope;
  security?: SecurityResult;
  evaluation?: Evaluation;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const len = (v: unknown) => (Array.isArray(v) ? v.length : 0);
const s = (v: unknown) => (v == null ? "" : String(v));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const provider = (v: unknown) => humanize(s(v)) || "The AI provider";

// ------------------------------------------------------------------ friendly notices

function noticeText(e: RunEvent): { text: string; tone: "info" | "warn" | "bad" } {
  const p = e.payload ?? {};
  switch (e.kind) {
    case "llm_retry":
      return { text: `${provider(p.provider)} was busy, retrying`, tone: "warn" };
    case "llm_repair":
      return { text: "The answer was malformed, asked again", tone: "warn" };
    case "llm_failover":
      return { text: `Switched to ${provider(p.to)}`, tone: "info" };
    case "llm_provider_failed":
      return { text: p.to ? `${provider(p.from)} is unavailable, trying ${provider(p.to)}` : `${provider(p.from)} is unavailable`, tone: "bad" };
    case "agent_output_rejected":
      return { text: "The answer did not pass checks, asked again", tone: "warn" };
    case "agent_escalation":
      return { text: `Moved to ${provider(p.to)} for a better answer`, tone: "info" };
    case "scope_violation": {
      const paths = Array.isArray(p.violations) ? (p.violations as ScopeViolation[]).map((v) => v.path).filter(Boolean) : [];
      return { text: `Tried to change a file outside the plan${paths.length ? `: ${paths.join(", ")}` : ""}`, tone: "bad" };
    }
    case "repeated_action_blocked":
      return { text: "The same change was submitted twice and sent back", tone: "warn" };
    case "duplicate_action_skipped":
      return { text: "Step already done, skipped", tone: "info" };
    case "qa_tests_rejected":
      return { text: "The tests were rejected and are being rewritten", tone: "warn" };
    case "triage_unavailable":
      return { text: "AI security review unavailable; automatic scan only", tone: "warn" };
    case "rubric_unavailable":
      return { text: "AI scoring unavailable", tone: "warn" };
    case "provider_switched":
      return { text: `Switched to ${provider(p.provider)}`, tone: "info" };
    default:
      return { text: humanize(e.kind), tone: "info" };
  }
}

export function NoticeRow({ event }: { event: RunEvent }) {
  const { text, tone } = noticeText(event);
  return (
    <p className="flex items-center gap-2 px-1 text-sm text-slate-600 dark:text-slate-400">
      <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", tone === "bad" ? "bg-rose-500" : tone === "warn" ? "bg-sun-400" : "bg-aqua-500")} />
      {text}
    </p>
  );
}

// ------------------------------------------------------------------ helpers

function latestCall(card: FeedCard, calls: Map<string, LiveCall>): LiveCall | undefined {
  for (let i = card.calls.length - 1; i >= 0; i--) {
    const c = calls.get(card.calls[i]);
    if (c) return c;
  }
  return undefined;
}

function stateFallback(node: string, state: RunState): unknown {
  switch (node) {
    case "analyst":
      return state.analysis;
    case "architect":
      return state.design;
    case "qa_author":
      return state.acceptance && (state.acceptance.content || state.acceptance.covered_criteria?.length) ? state.acceptance : undefined;
    case "security":
      return state.security?.triage ?? undefined;
    case "evaluate":
      return state.evaluation?.rubric ?? undefined;
    case "release":
      return state.release;
    default:
      return undefined;
  }
}

interface CardData {
  output: unknown;
  qa?: QAResult;
  scope?: Scope;
  security?: SecurityResult;
  evaluation?: Evaluation;
}

function cardData(card: FeedCard, run: RunDetail, snap?: CardSnapshot): CardData {
  const fallback = !card.hasOutput && card.isLatest && card.status === "done" ? stateFallback(card.node, run.state) : undefined;
  let qa = card.isLatest ? run.state.qa : snap?.qa;
  const sum = parseTestSummary(card.summary);
  if (qa && sum.passed != null && (qa.tests.passed !== sum.passed || (sum.failed != null && qa.tests.failed !== sum.failed))) qa = undefined;
  return {
    output: card.hasOutput ? card.output : fallback,
    qa,
    scope: card.isLatest ? run.state.scope : snap?.scope,
    security: card.isLatest ? run.state.security : snap?.security,
    evaluation: card.isLatest ? run.state.evaluation : snap?.evaluation,
  };
}

/** One plain sentence describing the result of a step. */
function summaryLine(card: FeedCard, d: CardData): string {
  if (card.status === "running") return card.calls.length || card.node === "qa_run" ? "Working…" : "Starting…";
  if (card.status === "paused") return "Paused";
  if (card.status === "failed") return "Stopped with an error";
  const o = isObj(d.output) ? d.output : null;
  switch (card.node) {
    case "analyst": {
      if (!o) break;
      const q = len(o.clarifying_questions);
      return `${plural(len(o.acceptance_criteria), "acceptance criterion").replace("criterions", "criteria")} and ${plural(len(o.tasks), "task")}${q ? `, ${plural(q, "question")} for you` : ""}`;
    }
    case "architect":
      if (o) return `Plans to change ${plural(len(o.impacted_components), "file")}`;
      break;
    case "qa_author":
      if (o) {
        const tests = (s(o.content).match(/^\s*def test_/gm) ?? []).length;
        return tests ? `Wrote ${plural(tests, "test")} covering ${plural(len(o.covered_criteria), "criterion").replace("criterions", "criteria")}` : `Covers ${len(o.covered_criteria)} criteria`;
      }
      break;
    case "developer":
      if (o) return `Edited ${plural(len(o.files), "file")}`;
      break;
    case "apply_change": {
      const blocked = card.violations.length > 0 || /reject|block/i.test(card.summary ?? "");
      if (blocked) return `Blocked: tried to change ${card.violations.map((v) => v.path).join(", ") || "a protected file"}`;
      return "All changes are within the approved files";
    }
    case "qa_run": {
      const sum = parseTestSummary(card.summary);
      const passed = d.qa?.tests.passed ?? sum.passed;
      const failed = (d.qa ? d.qa.tests.failed + d.qa.tests.errors : (sum.failed ?? 0) + (sum.errors ?? 0)) || 0;
      const cov = d.qa?.tests.coverage_percent ?? sum.coverage;
      if (passed == null) break;
      return `${passed} tests passed${failed ? `, ${failed} failed` : ""}${cov != null ? ` · ${cov}% of code tested` : ""}`;
    }
    case "security": {
      const sec = d.security;
      if (sec) {
        const b = sec.blocking.length;
        return b ? `${plural(b, "serious issue")} must be fixed` : sec.findings.length ? `${plural(sec.findings.length, "finding")}, none serious` : "No issues found";
      }
      break;
    }
    case "evaluate": {
      const parsed = parseEvalSummary(card.summary);
      const overall = toPercent(d.evaluation?.scores.overall ?? parsed.overall ?? null);
      const p = d.evaluation?.golden.passed ?? parsed.passed;
      const t = d.evaluation?.golden.total ?? parsed.total;
      if (overall != null) return `Score ${overall.toFixed(0)} of 100${p != null && t != null ? ` · ${p} of ${t} hidden tests passed` : ""}`;
      break;
    }
    case "release": {
      const rec = o ? s(o.recommendation) : "";
      if (rec) return rec === "approve" ? "Recommends approving the change" : rec === "reject" ? "Recommends rejecting the change" : "Recommends changes before approval";
      break;
    }
    case "merge_deploy":
      return "Merged and deployed";
    case "close":
      return "Run closed";
  }
  return card.status === "done" ? "Done" : "";
}

function Dot({ status, warn }: { status: FeedCard["status"]; warn?: boolean }) {
  return (
    <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden>
      {status === "running" && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-70" />}
      <span
        className={cx(
          "relative inline-flex h-2.5 w-2.5 rounded-full",
          status === "running" ? "bg-accent-500" : status === "failed" ? "bg-rose-600" : status === "paused" || warn ? "bg-sun-400" : "bg-accent-500",
        )}
      />
    </span>
  );
}

const STATUS_WORD: Record<FeedCard["status"], string> = { running: "In progress", done: "Done", failed: "Failed", paused: "Paused" };

// ------------------------------------------------------------------ card bodies

function Metric({ label, value, tone }: { label: string; value: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div>
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <p className={cx("text-xl font-semibold tabular-nums", tone === "good" ? "text-accent-700 dark:text-accent-400" : tone === "bad" ? "text-rose-700 dark:text-rose-300" : "text-slate-900 dark:text-white")}>{value}</p>
    </div>
  );
}

function TestsBody({ card, d }: { card: FeedCard; d: CardData }) {
  const sum = parseTestSummary(card.summary);
  const qa = d.qa;
  const passed = qa?.tests.passed ?? sum.passed;
  const failed = qa ? qa.tests.failed + qa.tests.errors : (sum.failed ?? 0) + (sum.errors ?? 0);
  const coverage = qa?.tests.coverage_percent ?? sum.coverage;
  const lint = qa ? qa.lint.issues.length : sum.lint;
  const failing = (qa?.tests.cases ?? []).filter((c) => c.outcome === "failed" || c.outcome === "error");
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="Passed" value={passed ?? "—"} tone="good" />
        <Metric label="Failed" value={failed} tone={failed ? "bad" : undefined} />
        <Metric label="Code tested" value={coverage != null ? `${coverage}%` : "—"} />
        <Metric label="Style issues" value={lint ?? "—"} />
      </div>
      {failing.length > 0 && (
        <Section title="Failing tests">
          <ul className="space-y-1.5">
            {failing.map((c) => (
              <li key={c.nodeid}>
                <details className="group rounded-lg bg-rose-50 dark:bg-rose-500/10">
                  <summary className="flex cursor-pointer select-none items-center gap-2 px-3 py-2 text-sm text-rose-900 dark:text-rose-200">
                    <XCircle className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 break-words">{(c.nodeid.split("::").pop() ?? c.nodeid).replace(/^test_/, "").replace(/_/g, " ")}</span>
                  </summary>
                  {c.message && <p className="px-3 pb-3 text-sm text-rose-800 dark:text-rose-200/90">{c.message}</p>}
                </details>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function ScopeBody({ card, d }: { card: FeedCard; d: CardData }) {
  const violations = card.violations.length ? card.violations : d.scope && !d.scope.ok ? d.scope.violations : [];
  if (violations.length)
    return (
      <ul className="space-y-1.5">
        {violations.map((v, i) => (
          <li key={i} className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:bg-rose-500/10 dark:text-rose-200">
            <span className="font-mono font-semibold">{v.path}</span> is protected and may not be changed.
          </li>
        ))}
      </ul>
    );
  const files = d.scope?.ok ? d.scope.files ?? [] : [];
  if (!files.length) return null;
  return (
    <Section title="Files changed">
      <p className="font-mono text-sm text-slate-700 dark:text-slate-300">{files.join(", ")}</p>
    </Section>
  );
}

function Body({ card, d, call }: { card: FeedCard; d: CardData; call?: LiveCall }) {
  if (card.status === "running" || card.status === "paused") {
    if (card.node === "qa_run" || card.node === "apply_change") return <p className="text-slate-500">Running…</p>;
    return <StreamView text={call?.text ?? ""} active={card.status === "running"} />;
  }
  if (card.status === "failed") return <p className="text-sm text-rose-700 dark:text-rose-300">This step stopped with an error. You can resume the run below.</p>;
  const o = d.output;
  switch (card.node) {
    case "analyst":
      return o ? <AnalystOutput output={o} /> : null;
    case "architect":
      return o ? <ArchitectOutput output={o} /> : null;
    case "qa_author":
      return o ? <QAOutput output={o} /> : null;
    case "developer":
      return o ? <DeveloperOutput output={o} /> : null;
    case "apply_change":
      return <ScopeBody card={card} d={d} />;
    case "qa_run":
      return <TestsBody card={card} d={d} />;
    case "security":
      return o || d.security?.findings.length ? <SecurityOutput triage={o ?? null} findings={d.security?.findings} /> : null;
    case "evaluate":
      return o ? <RubricOutput output={o} /> : null;
    case "release":
      return o ? <ReleaseOutput output={o} /> : null;
    default:
      return null;
  }
}

export function StepCard({
  card,
  calls,
  run,
  snap,
  open,
  onToggle,
}: {
  card: FeedCard;
  calls: Map<string, LiveCall>;
  run: RunDetail;
  snap?: CardSnapshot;
  open: boolean;
  onToggle: () => void;
}) {
  const call = latestCall(card, calls);
  const d = cardData(card, run, snap);
  const body = <Body card={card} d={d} call={call} />;
  const hasBody = card.status !== "done" || !["merge_deploy", "close"].includes(card.node);
  const title = TITLE[card.node] ?? nodeLabel(card.node);

  return (
    <article className={cx("card animate-fade-in overflow-hidden", card.status === "running" && "border-accent-300 dark:border-accent-500/40")}>
      <button
        type="button"
        onClick={onToggle}
        disabled={!hasBody}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left disabled:cursor-default"
      >
        <Dot status={card.status} warn={card.status === "done" && needsAttention(card, d)} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-base font-semibold text-slate-900 dark:text-white">{title}</span>
            {card.attempt > 1 && <span className="text-sm text-slate-500 dark:text-slate-400">attempt {card.attempt}</span>}
          </span>
          <span className="block text-sm text-slate-600 dark:text-slate-400">{summaryLine(card, d)}</span>
        </span>
        <span className="sr-only">{STATUS_WORD[card.status]}</span>
        {hasBody && <ChevronDown className={cx("h-5 w-5 shrink-0 text-slate-400 transition-transform", open && "rotate-180")} />}
      </button>
      {open && hasBody && (
        <div className="space-y-3 border-t border-slate-100 px-4 py-4 dark:border-slate-800">
          {card.notices.map((n) => (
            <NoticeRow key={n.id} event={n} />
          ))}
          {body}
        </div>
      )}
      {!open && card.notices.length > 0 && card.status === "done" && (
        <div className="px-4 pb-3 pl-[2.375rem]">
          <NoticeRow event={card.notices[card.notices.length - 1]} />
        </div>
      )}
    </article>
  );
}

// ------------------------------------------------------------------ gates, halts, outcome

function Resolved({ tone, children }: { tone: "good" | "warn" | "bad"; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <span className={cx("mt-2 h-2.5 w-2.5 shrink-0 rounded-full", tone === "good" ? "bg-accent-500" : tone === "warn" ? "bg-sun-400" : "bg-rose-600")} />
      <div className="min-w-0 text-slate-700 dark:text-slate-300">{children}</div>
    </div>
  );
}

export function GateItem({ item, run, onResumed }: { item: FeedGate; run: RunDetail; onResumed: (s: RunSummary) => void }) {
  if (item.active) return <GatePanel run={run} onDone={onResumed} />;
  const ans = item.answered?.payload;
  const actor = s(ans?.actor) || "a reviewer";
  const raw = ans?.decision;
  const decision = typeof raw === "string" ? raw : isObj(raw) ? s(raw.decision) : "";
  const g = item.gate;
  if (g.gate === "clarification")
    return <Resolved tone={item.answered ? "good" : "warn"}>{item.answered ? `You answered the analyst's questions (${actor}).` : "The analyst asked questions."}</Resolved>;
  if (g.gate === "release_approval") {
    const verb = decision === "approve" ? "approved" : decision === "reject" ? "rejected" : "asked for changes to";
    return <Resolved tone={decision === "approve" ? "good" : decision === "reject" ? "bad" : "warn"}>{item.answered ? `${actor} ${verb} the release.` : "Release approval was requested."}</Resolved>;
  }
  const choice = decision === "retry" ? "chose to try again" : decision === "override" ? "accepted the risk and continued" : decision === "abort" ? "stopped the run" : "decided";
  return <Resolved tone="warn">{item.answered ? `A decision was needed. ${actor} ${choice}.` : "A decision was needed."}</Resolved>;
}

export function HaltItem({ item, run, onResumed }: { item: FeedHalt; run: RunDetail; onResumed: (s: RunSummary) => void }) {
  if (item.active) return <GatePanel run={run} onDone={onResumed} />;
  return <Resolved tone="warn">{item.kind === "paused" ? "The AI provider was unavailable; the run paused and was resumed." : "The run stopped with an error and was resumed."}</Resolved>;
}

export function OutcomeItem({ item, run }: { item: FeedOutcome; run: RunDetail }) {
  if (["completed", "rejected", "aborted"].includes(run.status)) return <OutcomeBanner run={run} />;
  return <Resolved tone="good">Finished: {humanize(s(item.event.payload.outcome))}</Resolved>;
}

/** A finished step whose result was negative (blocked change, failing tests, serious security issue). */
function needsAttention(card: FeedCard, d: CardData): boolean {
  if (card.node === "apply_change") return card.violations.length > 0 || /reject|block/i.test(card.summary ?? "");
  if (card.node === "qa_run") {
    const sum = parseTestSummary(card.summary);
    const failed = d.qa ? d.qa.tests.failed + d.qa.tests.errors : (sum.failed ?? 0) + (sum.errors ?? 0);
    return failed > 0;
  }
  if (card.node === "security") return (d.security?.blocking.length ?? 0) > 0;
  return false;
}
