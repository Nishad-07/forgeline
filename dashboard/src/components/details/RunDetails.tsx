import { ChevronRight } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { useAsync } from "../../hooks/useAsync";
import type { RunStream } from "../../hooks/useRunStream";
import { clockTime, compactNumber, formatCost, formatDuration, humanize } from "../../lib/format";
import type { Gate, RunDetail, RunEvent } from "../../types";
import { DeveloperOutput, ReleaseOutput, RubricOutput, SecurityOutput } from "../console/outputs";
import { WhatWasBuilt } from "../run/WhatWasBuilt";
import { EventTimeline } from "../run/EventTimeline";
import { DiffViewer } from "../run/DiffViewer";
import { toPercent } from "../ui/Gauge";
import { cx, EmptyState, ErrorState, LoadingBlock } from "../ui/primitives";
import { Tabs, type TabItem } from "../ui/Tabs";

// ------------------------------------------------------------------ building blocks

function Block({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cx("min-w-0", className)}>
      <h3 className="mb-3 text-lg font-semibold text-ink-900 dark:text-white">{title}</h3>
      {children}
    </section>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return <p className="text-slate-500 dark:text-slate-400">{children}</p>;
}

function Tile({ label, value, caption, tone }: { label: string; value: ReactNode; caption: string; tone?: "good" | "bad" }) {
  return (
    <div className="card px-4 py-4">
      <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className={cx("mt-1 text-2xl font-semibold tabular-nums", tone === "good" ? "text-accent-700 dark:text-accent-400" : tone === "bad" ? "text-rose-700 dark:text-rose-300" : "text-slate-900 dark:text-white")}>
        {value}
      </p>
      <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{caption}</p>
    </div>
  );
}

export function SummaryTiles({ run }: { run: RunDetail }) {
  const s = run.state;
  const t = s.qa?.tests;
  const failed = t ? t.failed + t.errors : 0;
  const score = toPercent(s.evaluation?.scores.overall ?? null);
  const blocking = s.security?.blocking.length ?? 0;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label="Tests" value={t ? `${t.passed} / ${t.passed + failed}` : "—"} caption={t ? (failed ? `${failed} failing` : "All passing") : "Not run yet"} tone={t ? (failed ? "bad" : "good") : undefined} />
      <Tile label="Coverage" value={t?.coverage_percent != null ? `${t.coverage_percent}%` : "—"} caption="Share of code the tests run" />
      <Tile
        label="Security"
        value={s.security ? s.security.findings.length : "—"}
        caption={s.security ? (blocking ? `${blocking} serious, must be fixed` : "Findings, none serious") : "Not reviewed yet"}
        tone={s.security ? (blocking ? "bad" : "good") : undefined}
      />
      <Tile label="Score" value={score != null ? score.toFixed(0) : "—"} caption="Overall quality, out of 100" tone={score != null ? (score >= 80 ? "good" : "bad") : undefined} />
    </div>
  );
}

// ------------------------------------------------------------------ Overview

function Overview({ run }: { run: RunDetail }) {
  const a = run.state.analysis;
  const d = run.state.design;
  const decisions = run.state.human_decisions ?? [];
  return (
    <div className="space-y-8">
      {(run.state.release || run.status === "aborted") && <WhatWasBuilt run={run} />}
      <Block title="Plan">
        {!a ? (
          <Muted>The analyst has not finished the plan yet.</Muted>
        ) : (
          <div className="space-y-5">
            <p className="text-slate-700 dark:text-slate-300">{a.summary}</p>
            <div>
              <h4 className="mb-2 font-semibold text-slate-900 dark:text-white">Acceptance criteria</h4>
              <ol className="space-y-3">
                {a.acceptance_criteria.map((c) => (
                  <li key={c.id} className="text-slate-700 dark:text-slate-300">
                    <span className="mr-2 font-semibold text-ink-900 dark:text-accent-300">{c.id}</span>
                    Given {c.given}, when {c.when}, then {c.then}.
                  </li>
                ))}
              </ol>
            </div>
            <div>
              <h4 className="mb-2 font-semibold text-slate-900 dark:text-white">Tasks</h4>
              <ul className="space-y-2">
                {a.tasks.map((t) => (
                  <li key={t.id} className="text-slate-700 dark:text-slate-300">
                    <span className="font-medium text-slate-900 dark:text-white">{t.title}.</span> {t.description}
                  </li>
                ))}
              </ul>
            </div>
            {run.state.clarifications && run.state.clarifications.length > 0 && (
              <div>
                <h4 className="mb-2 font-semibold text-slate-900 dark:text-white">Your answers</h4>
                <dl className="space-y-2">
                  {run.state.clarifications.map((c, i) => (
                    <div key={i}>
                      <dt className="text-slate-500 dark:text-slate-400">{c.question}</dt>
                      <dd className="text-slate-900 dark:text-white">{c.answer}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </div>
        )}
      </Block>

      <Block title="Design">
        {!d ? (
          <Muted>The architect has not finished the design yet.</Muted>
        ) : (
          <div className="space-y-3">
            <p className="text-slate-700 dark:text-slate-300">{d.summary}</p>
            <h4 className="font-semibold text-slate-900 dark:text-white">Files to change</h4>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {d.impacted_components.map((c) => (
                <li key={c.path} className="py-2.5">
                  <p className="font-mono text-sm font-semibold text-slate-900 dark:text-white">
                    {c.path} <span className="ml-1 font-sans font-normal text-slate-500">{c.change_type === "create" ? "new file" : "edit"}</span>
                  </p>
                  <p className="text-slate-600 dark:text-slate-400">{c.reason}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Block>

      <Block title="Decisions">
        {decisions.length === 0 ? (
          <Muted>No decisions yet.</Muted>
        ) : (
          <ul className="space-y-2">
            {decisions.map((h, i) => (
              <li key={i} className="text-slate-700 dark:text-slate-300">
                <span className="font-medium text-slate-900 dark:text-white">{h.actor}</span>{" "}
                {h.gate === "clarification" ? "answered the analyst's questions" : h.gate === "release_approval" ? `${h.decision === "approve" ? "approved" : h.decision === "reject" ? "rejected" : "asked for changes to"} the release` : `chose "${humanize(h.decision ?? "")}"`}
                {h.comment && h.gate !== "clarification" ? `: "${h.comment}"` : ""}
                <span className="ml-2 text-sm text-slate-400">{clockTime(h.ts)}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  );
}

// ------------------------------------------------------------------ Code

function Code({ run, version, outputs }: { run: RunDetail; version: number; outputs: RunEvent[] }) {
  const diff = useAsync(() => api.diff(run.id), [run.id, version]);
  const attempts = run.state.attempts_log ?? [];
  const lastDev = [...outputs].reverse().find((e) => e.payload.agent === "developer");
  return (
    <div className="space-y-8">
      <Block title="Changes">
        {diff.error && !diff.data ? (
          <ErrorState error="The changes could not be loaded." onRetry={diff.reload} />
        ) : !diff.data ? (
          <LoadingBlock label="Loading changes" />
        ) : !diff.data.diff.trim() ? (
          <Muted>No code has been written yet.</Muted>
        ) : (
          <>
            <p className="mb-3 text-slate-600 dark:text-slate-400">
              {diff.data.stat.files} files, <span className="text-accent-700 dark:text-accent-400">{diff.data.stat.added} lines added</span>,{" "}
              <span className="text-rose-700 dark:text-rose-300">{diff.data.stat.removed} removed</span>
            </p>
            <DiffViewer diff={diff.data.diff} />
          </>
        )}
      </Block>
      <Block title="Attempts">
        {attempts.length === 0 ? (
          <Muted>No attempts yet.</Muted>
        ) : (
          <ol className="space-y-2">
            {attempts.map((a, i) => {
              const ok = a.stage === "qa" && a.scope_ok && (a.failed ?? 0) + (a.errors ?? 0) === 0;
              return (
                <li key={i} className="flex items-start gap-3 text-slate-700 dark:text-slate-300">
                  <span className={cx("mt-2 h-2.5 w-2.5 shrink-0 rounded-full", ok ? "bg-accent-500" : "bg-rose-600")} />
                  <span>
                    <span className="font-medium text-slate-900 dark:text-white">Attempt {a.attempt}: </span>
                    {a.stage === "scope"
                      ? `blocked, tried to change ${a.violations?.map((v) => v.path).join(", ") || "a protected file"}`
                      : `${a.passed ?? 0} tests passed, ${(a.failed ?? 0) + (a.errors ?? 0)} failed${a.coverage != null ? `, ${a.coverage}% of code tested` : ""}`}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </Block>
      {lastDev && (
        <Block title="Developer's latest explanation">
          <DeveloperOutput output={lastDev.payload.output} />
        </Block>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Checks

function Checks({ run }: { run: RunDetail }) {
  const s = run.state;
  const t = s.qa?.tests;
  const failing = (t?.cases ?? []).filter((c) => c.outcome === "failed" || c.outcome === "error");
  const ev = s.evaluation;
  return (
    <div className="space-y-8">
      <Block title="Tests">
        {!t ? (
          <Muted>Tests have not run yet.</Muted>
        ) : (
          <div className="space-y-2">
            <p className="text-slate-700 dark:text-slate-300">
              {t.passed} passed, {t.failed + t.errors} failed. {t.coverage_percent != null && `${t.coverage_percent}% of the code is exercised by tests.`}
            </p>
            {failing.map((c) => (
              <p key={c.nodeid} className="rounded-lg bg-rose-50 px-3 py-2 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200">
                {(c.nodeid.split("::").pop() ?? "").replace(/^test_/, "").replace(/_/g, " ")}: {c.message}
              </p>
            ))}
          </div>
        )}
      </Block>
      <Block title="Code style">
        {!s.qa ? (
          <Muted>Not checked yet.</Muted>
        ) : s.qa.lint.issues.length === 0 ? (
          <p className="text-slate-700 dark:text-slate-300">No style issues.</p>
        ) : (
          <ul className="space-y-1 font-mono text-sm text-slate-700 dark:text-slate-300">
            {s.qa.lint.issues.map((l, i) => (
              <li key={i}>
                {l.file}:{l.line} {l.message}
              </li>
            ))}
          </ul>
        )}
      </Block>
      <Block title="Security">
        {!s.security ? <Muted>Not reviewed yet.</Muted> : <SecurityOutput triage={s.security.triage} findings={s.security.findings} />}
      </Block>
      <Block title="Evaluation">
        {!ev ? (
          <Muted>Not evaluated yet.</Muted>
        ) : (
          <div className="space-y-4">
            <p className="text-slate-700 dark:text-slate-300">
              {ev.golden.available ? `${ev.golden.passed} of ${ev.golden.total} hidden tests passed. ` : ""}
              {ev.scores.overall != null && `Overall score ${toPercent(ev.scores.overall)?.toFixed(0)} of 100.`}
            </p>
            {ev.rubric && <RubricOutput output={ev.rubric} />}
          </div>
        )}
      </Block>
      {s.release && (
        <Block title="Release summary">
          <ReleaseOutput output={s.release} />
        </Block>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Activity

const MILESTONE_NODE: Record<string, string> = {
  analyst: "Plan ready",
  architect: "Design ready",
  qa_author: "Tests written",
  developer: "Code written",
  security: "Security review done",
  evaluate: "Evaluation done",
  release: "Release summary ready",
  merge_deploy: "Merged and deployed",
};

const GATE_TEXT: Record<Gate["gate"], string> = {
  clarification: "Waiting for your answers",
  release_approval: "Waiting for your approval",
  escalation: "Waiting for your decision",
};

function milestone(e: RunEvent): { text: string; tone: "good" | "warn" | "bad" } | null {
  const p = e.payload ?? {};
  switch (e.kind) {
    case "run_created":
      return { text: "Run started", tone: "good" };
    case "node_end": {
      if (e.node === "apply_change") return /reject|block/i.test(String(p.summary ?? "")) ? { text: "Change blocked by the change check", tone: "bad" } : null;
      if (e.node === "qa_run") return / 0 failed/.test(String(p.summary ?? "")) ? { text: "All tests passed", tone: "good" } : { text: "Some tests failed", tone: "bad" };
      const t = e.node ? MILESTONE_NODE[e.node] : undefined;
      return t ? { text: t, tone: "good" } : null;
    }
    case "gate_opened": {
      const g = p.gate as Gate | undefined;
      return g ? { text: GATE_TEXT[g.gate], tone: "warn" } : null;
    }
    case "gate_answered":
      return { text: `${String(p.actor ?? "Reviewer")} responded`, tone: "good" };
    case "llm_failover":
    case "provider_switched":
      return { text: `Switched to ${humanize(String(p.to ?? p.provider ?? ""))}`, tone: "warn" };
    case "run_paused":
      return { text: "Paused: AI provider unavailable", tone: "warn" };
    case "run_failed":
      return { text: "Stopped with an error", tone: "bad" };
    case "run_finished":
      return { text: `Finished: ${humanize(String(p.outcome ?? ""))}`, tone: "good" };
    default:
      return null;
  }
}

function UsageAndAudit({ run, version }: { run: RunDetail; version: number }) {
  const usage = useAsync(() => api.usage(run.id), [run.id, version]);
  const audit = useAsync(() => api.audit(run.id), [run.id, version]);
  const rows = usage.data?.by_agent ?? [];
  const t = usage.data?.totals;
  return (
    <div className="space-y-4 pt-3">
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800 dark:text-slate-400">
              <th className="py-2 pr-4 font-medium">Agent</th>
              <th className="py-2 pr-4 font-medium">AI calls</th>
              <th className="py-2 pr-4 font-medium">Tokens</th>
              <th className="py-2 pr-4 font-medium">Time</th>
              <th className="py-2 font-medium">Estimated cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.agent}-${r.provider}`} className="border-b border-slate-100 dark:border-slate-800">
                <td className="py-2 pr-4 text-slate-900 dark:text-white">
                  {humanize(r.agent)} <span className="text-slate-400">({humanize(r.provider)})</span>
                </td>
                <td className="py-2 pr-4 tabular-nums">{r.calls}</td>
                <td className="py-2 pr-4 tabular-nums">{compactNumber(r.input_tokens + r.output_tokens)}</td>
                <td className="py-2 pr-4 tabular-nums">{formatDuration(r.latency_ms)}</td>
                <td className="py-2 tabular-nums">{formatCost(r.cost_usd)}</td>
              </tr>
            ))}
            {t && rows.length > 0 && (
              <tr className="font-semibold text-slate-900 dark:text-white">
                <td className="py-2 pr-4">Total</td>
                <td className="py-2 pr-4 tabular-nums">{t.calls}</td>
                <td className="py-2 pr-4 tabular-nums">{compactNumber((t.input_tokens ?? 0) + (t.output_tokens ?? 0))}</td>
                <td className="py-2 pr-4 tabular-nums">{formatDuration(t.latency_ms)}</td>
                <td className="py-2 tabular-nums">{formatCost(t.cost_usd)}</td>
              </tr>
            )}
          </tbody>
        </table>
        {usage.data && rows.length === 0 && <Muted>No AI calls yet.</Muted>}
      </div>
      {audit.data && (
        <p className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
          <span className={cx("h-2.5 w-2.5 rounded-full", audit.data.verification.ok ? "bg-accent-500" : "bg-rose-600")} />
          Audit log: {audit.data.verification.ok ? "chain intact" : "chain broken"}, {audit.data.verification.entries} entries
        </p>
      )}
    </div>
  );
}

function Activity({ run, stream }: { run: RunDetail; stream: RunStream }) {
  const [all, setAll] = useState(false);
  const items = useMemo(
    () =>
      stream.events
        .map((e) => ({ e, m: milestone(e) }))
        .filter((x): x is { e: RunEvent; m: NonNullable<ReturnType<typeof milestone>> } => !!x.m),
    [stream.events],
  );
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold text-ink-900 dark:text-white">Timeline</h3>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} className="h-4 w-4 accent-[#4f46e5]" />
          All events
        </label>
      </div>
      {all ? (
        <div className="card overflow-hidden">
          <EventTimeline events={stream.events} className="h-[480px]" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState title="Nothing has happened yet" className="py-8" />
      ) : (
        <ol className="space-y-2.5">
          {items.map(({ e, m }) => (
            <li key={e.id} className="flex items-start gap-3">
              <span className="w-20 shrink-0 pt-0.5 text-sm tabular-nums text-slate-400">{clockTime(e.ts)}</span>
              <span className={cx("mt-2 h-2.5 w-2.5 shrink-0 rounded-full", m.tone === "good" ? "bg-accent-500" : m.tone === "warn" ? "bg-sun-400" : "bg-rose-600")} />
              <span className="text-slate-800 dark:text-slate-200">{m.text}</span>
            </li>
          ))}
        </ol>
      )}
      <details className="group border-t border-slate-200 pt-4 dark:border-slate-800">
        <summary className="inline-flex cursor-pointer select-none items-center gap-1.5 font-semibold text-ink-900 dark:text-white">
          <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
          Usage and audit
        </summary>
        <UsageAndAudit run={run} version={stream.version} />
      </details>
    </div>
  );
}

// ------------------------------------------------------------------ tabs

type TabId = "overview" | "code" | "checks" | "activity";

export function RunDetails({ run, stream, tab, onTab }: { run: RunDetail; stream: RunStream; tab: string; onTab: (t: TabId) => void }) {
  const active: TabId = (["overview", "code", "checks", "activity"] as const).includes(tab as TabId) ? (tab as TabId) : "overview";
  const items: TabItem<TabId>[] = [
    { id: "overview", label: "Overview" },
    { id: "code", label: "Code" },
    { id: "checks", label: "Checks" },
    { id: "activity", label: "Activity" },
  ];
  const outputs = stream.events.filter((e) => e.kind === "agent_output");
  return (
    <div className="space-y-5">
      <SummaryTiles run={run} />
      <section className="card">
        <div className="px-3 sm:px-5">
          <Tabs items={items} active={active} onChange={onTab} />
        </div>
        <div className="p-4 sm:p-6">
          {active === "overview" && <Overview run={run} />}
          {active === "code" && <Code run={run} version={stream.version} outputs={outputs} />}
          {active === "checks" && <Checks run={run} />}
          {active === "activity" && <Activity run={run} stream={stream} />}
        </div>
      </section>
    </div>
  );
}
