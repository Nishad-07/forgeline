import {
  ArrowLeftRight,
  ArrowUpRight,
  CheckCircle2,
  Circle,
  CloudOff,
  Copy,
  FileWarning,
  Flag,
  FlaskConical,
  Hand,
  PauseCircle,
  PlayCircle,
  Repeat,
  Rocket,
  RotateCw,
  ShieldX,
  Shuffle,
  Sparkles,
  UserCheck,
  Wrench,
  XCircle,
  XOctagon,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { absoluteTime, clockTime, compactNumber, formatDuration, GATE_LABEL, humanize, nodeLabel, parseTs } from "../../lib/format";
import type { Gate, RunEvent } from "../../types";
import { cx, EmptyState } from "../ui/primitives";

type Tone = "slate" | "blue" | "green" | "amber" | "orange" | "red" | "violet" | "cyan";

interface Meta {
  icon: typeof Circle;
  tone: Tone;
  highlight?: boolean;
  title: (e: RunEvent) => ReactNode;
  detail?: (e: RunEvent) => ReactNode;
}

const p = (e: RunEvent, k: string) => e.payload?.[k];
const s = (v: unknown) => (v == null ? "" : String(v));

function ProblemList({ items }: { items: unknown }) {
  if (!Array.isArray(items) || !items.length) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {items.slice(0, 5).map((it, i) => (
        <li key={i} className="flex gap-1.5">
          <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-current opacity-60" />
          <span className="break-words">{typeof it === "string" ? it : it && typeof it === "object" && "path" in it ? `${s((it as Record<string, unknown>).path)}: ${s((it as Record<string, unknown>).reason ?? (it as Record<string, unknown>).rule)}` : JSON.stringify(it)}</span>
        </li>
      ))}
    </ul>
  );
}

const META: Record<string, Meta> = {
  run_created: { icon: Rocket, tone: "blue", title: () => "Run created", detail: (e) => `${s(p(e, "requirement"))} · provider ${s(p(e, "provider"))}` },
  node_start: { icon: PlayCircle, tone: "slate", title: (e) => `${nodeLabel(e.node)} started` },
  node_end: { icon: CheckCircle2, tone: "green", title: (e) => `${nodeLabel(e.node)} finished`, detail: (e) => s(p(e, "summary")) },
  node_error: { icon: XCircle, tone: "red", highlight: true, title: (e) => `${nodeLabel(e.node)} error`, detail: (e) => s(p(e, "error")) },
  llm_call: {
    icon: Sparkles,
    tone: "cyan",
    title: (e) => `${nodeLabel(e.node)} called ${s(p(e, "provider"))}`,
    detail: (e) => {
      const parts = [s(p(e, "model"))];
      if (p(e, "cached")) parts.push("served from cache");
      if (p(e, "input_tokens") != null) parts.push(`${compactNumber(Number(p(e, "input_tokens")))} in / ${compactNumber(Number(p(e, "output_tokens")))} out`);
      if (p(e, "latency_ms") != null) parts.push(formatDuration(Number(p(e, "latency_ms"))));
      if (Number(p(e, "repairs")) > 0) parts.push(`${p(e, "repairs")} repair(s)`);
      return parts.filter(Boolean).join(" · ");
    },
  },
  agent_output: { icon: CheckCircle2, tone: "green", title: (e) => `${nodeLabel(s(p(e, "agent")) || e.node)} output ready` },
  llm_retry: { icon: RotateCw, tone: "amber", title: (e) => `Retrying ${s(p(e, "provider"))} in ${s(p(e, "delay_s"))}s`, detail: (e) => s(p(e, "error")) },
  llm_repair: { icon: Wrench, tone: "amber", highlight: true, title: (e) => `Malformed output repaired (re-prompt ${s(p(e, "repair"))})`, detail: (e) => s(p(e, "error")) },
  llm_failover: {
    icon: ArrowLeftRight,
    tone: "orange",
    highlight: true,
    title: (e) => `Provider failover: ${s(p(e, "from"))} → ${s(p(e, "to"))}`,
    detail: (e) => s(p(e, "error")),
  },
  llm_provider_failed: {
    icon: CloudOff,
    tone: "red",
    highlight: true,
    title: (e) => `Provider ${s(p(e, "from"))} failed${p(e, "to") ? ` → trying ${s(p(e, "to"))}` : ""}`,
    detail: (e) => s(p(e, "error")),
  },
  agent_output_rejected: {
    icon: FileWarning,
    tone: "red",
    highlight: true,
    title: (e) => `${nodeLabel(e.node)} output rejected (attempt ${s(p(e, "attempt"))})`,
    detail: (e) => <ProblemList items={p(e, "problems")} />,
  },
  agent_escalation: {
    icon: ArrowUpRight,
    tone: "orange",
    highlight: true,
    title: (e) => `${nodeLabel(e.node)} escalated ${s(p(e, "from"))} → ${s(p(e, "to"))}`,
    detail: (e) => <ProblemList items={p(e, "problems")} />,
  },
  scope_violation: {
    icon: ShieldX,
    tone: "red",
    highlight: true,
    title: () => "Scope guard blocked the change",
    detail: (e) => <ProblemList items={p(e, "violations")} />,
  },
  repeated_action_blocked: { icon: Repeat, tone: "red", highlight: true, title: () => "Repeated no-op action blocked", detail: () => "Identical files resubmitted." },
  duplicate_action_skipped: { icon: Copy, tone: "slate", title: () => "Duplicate action skipped", detail: (e) => s(p(e, "action")) },
  qa_tests_rejected: { icon: FlaskConical, tone: "red", highlight: true, title: () => "Acceptance tests rejected", detail: (e) => s(p(e, "error")) },
  gate_opened: {
    icon: Hand,
    tone: "amber",
    highlight: true,
    title: (e) => {
      const g = p(e, "gate") as Gate | undefined;
      return `Human gate opened: ${g ? GATE_LABEL[g.gate] ?? g.gate : "gate"}`;
    },
    detail: (e) => {
      const g = p(e, "gate") as Gate | undefined;
      if (!g) return null;
      if (g.gate === "clarification") return `${g.questions.length} question(s) for a human`;
      if (g.gate === "release_approval") return `Recommendation: ${humanize(g.recommendation)}`;
      return `${humanize(g.reason)} at ${nodeLabel(g.node)}`;
    },
  },
  gate_answered: {
    icon: UserCheck,
    tone: "green",
    highlight: true,
    title: (e) => `${s(p(e, "actor")) || "Human"} answered ${GATE_LABEL[s(p(e, "gate"))] ?? humanize(s(p(e, "gate")))}`,
    detail: (e) => {
      const d = p(e, "decision");
      if (d == null) return null;
      return typeof d === "string" ? `Decision: ${humanize(d)}` : typeof d === "object" && d && "decision" in d ? `Decision: ${humanize(s((d as Record<string, unknown>).decision))}` : null;
    },
  },
  provider_switched: { icon: Shuffle, tone: "violet", highlight: true, title: (e) => `Provider switched to ${s(p(e, "provider"))}` },
  run_paused: { icon: PauseCircle, tone: "orange", highlight: true, title: (e) => `Run paused: ${s(p(e, "reason"))}`, detail: (e) => s(p(e, "error")) },
  run_failed: { icon: XOctagon, tone: "red", highlight: true, title: () => "Run failed", detail: (e) => s(p(e, "error")) },
  run_finished: { icon: Flag, tone: "green", highlight: true, title: (e) => `Run finished: ${humanize(s(p(e, "outcome")))}` },
};

const TONE_ICON: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
  blue: "bg-brand-100 text-brand-600 dark:bg-brand-500/20 dark:text-brand-300",
  green: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-300",
  amber: "bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300",
  orange: "bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-300",
  red: "bg-rose-100 text-rose-600 dark:bg-rose-500/20 dark:text-rose-300",
  violet: "bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-300",
  cyan: "bg-cyan-100 text-cyan-700 dark:bg-cyan-500/20 dark:text-cyan-300",
};

const TONE_HL: Record<Tone, string> = {
  slate: "",
  blue: "border-brand-200 bg-brand-50/60 dark:border-brand-500/25 dark:bg-brand-500/5",
  green: "border-emerald-200 bg-emerald-50/60 dark:border-emerald-500/25 dark:bg-emerald-500/5",
  amber: "border-amber-200 bg-amber-50/70 dark:border-amber-500/25 dark:bg-amber-500/5",
  orange: "border-orange-200 bg-orange-50/70 dark:border-orange-500/25 dark:bg-orange-500/5",
  red: "border-rose-200 bg-rose-50/70 dark:border-rose-500/25 dark:bg-rose-500/5",
  violet: "border-violet-200 bg-violet-50/60 dark:border-violet-500/25 dark:bg-violet-500/5",
  cyan: "",
};

const FALLBACK: Meta = { icon: Circle, tone: "slate", title: (e) => humanize(e.kind), detail: (e) => s(p(e, "error")) };

type Filter = "key" | "all";

export function EventTimeline({ events, className }: { events: RunEvent[]; className?: string }) {
  const [filter, setFilter] = useState<Filter>("key");
  const shown = useMemo(() => {
    const visible = events.filter((e) => e.kind !== "llm_delta");
    const list = filter === "all" ? visible : visible.filter((e) => e.kind !== "node_start" && e.kind !== "llm_call" && e.kind !== "agent_output");
    return [...list].reverse();
  }, [events, filter]);
  const startTs = events[0]?.ts;

  return (
    <div className={cx("flex min-h-0 flex-col", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2 dark:border-slate-800">
        <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-xs font-medium dark:bg-slate-800">
          {(["key", "all"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cx(
                "rounded-md px-2.5 py-1 transition-colors",
                filter === f ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white" : "text-slate-500 hover:text-slate-700 dark:text-slate-400",
              )}
            >
              {f === "key" ? "Key events" : "Everything"}
            </button>
          ))}
        </div>
        <span className="text-xs tabular-nums text-slate-400">{events.length} events</span>
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {shown.length === 0 ? (
          <EmptyState title="No events yet" />
        ) : (
          <ol className="relative space-y-1.5">
            {shown.map((e) => {
              const meta = META[e.kind] ?? FALLBACK;
              const Icon = meta.icon;
              const detail = meta.detail?.(e);
              const elapsed = startTs ? parseTs(e.ts) - parseTs(startTs) : NaN;
              const duration = e.kind === "node_end" ? Number(p(e, "duration_ms")) : NaN;
              return (
                <li
                  key={e.id}
                  className={cx(
                    "flex animate-fade-in gap-2.5 rounded-lg border px-2.5 py-2",
                    meta.highlight ? TONE_HL[meta.tone] || "border-transparent" : "border-transparent hover:bg-slate-50 dark:hover:bg-slate-800/40",
                  )}
                >
                  <span className={cx("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full", TONE_ICON[meta.tone])}>
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[13px] font-medium leading-snug text-slate-900 dark:text-slate-100">{meta.title(e)}</p>
                      <span className="shrink-0 pt-0.5 font-mono text-[10.5px] tabular-nums text-slate-400" title={absoluteTime(e.ts)}>
                        {clockTime(e.ts)}
                      </span>
                    </div>
                    {detail ? <div className="mt-0.5 break-words text-xs leading-relaxed text-slate-600 dark:text-slate-400">{detail}</div> : null}
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px] text-slate-400">
                      {e.node && <span className="rounded bg-slate-100 px-1.5 py-px font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{nodeLabel(e.node)}</span>}
                      {!Number.isNaN(duration) && <span className="rounded bg-emerald-50 px-1.5 py-px font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">{formatDuration(duration)}</span>}
                      {!Number.isNaN(elapsed) && elapsed > 0 && <span className="tabular-nums">+{formatDuration(elapsed)}</span>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
