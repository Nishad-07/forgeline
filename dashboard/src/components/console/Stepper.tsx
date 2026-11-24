import { Check, Hand, Pause, X } from "lucide-react";
import { STAGES } from "../../lib/feed";
import type { GraphProgress, NodeRunState } from "../../lib/nodeStatus";
import type { RunDetail } from "../../types";
import { cx } from "../ui/primitives";

const ESCALATION_NODE: Record<string, string> = { evaluator: "evaluate", qa: "qa_author" };

function nodeState(id: string, progress: GraphProgress, run: RunDetail | undefined): NodeRunState {
  let state = progress.nodes[id]?.state ?? "pending";
  if (run?.status === "awaiting_human" && run.gate?.gate === "escalation") {
    const raised = ESCALATION_NODE[run.gate.node] ?? run.gate.node;
    if (raised === id) state = "waiting";
  }
  return state;
}

/** Aggregates the workflow steps of one plain-language stage into a single state. */
function stageState(nodes: string[], progress: GraphProgress, run: RunDetail | undefined, laterStarted: boolean): NodeRunState {
  const states = nodes.map((n) => nodeState(n, progress, run));
  for (const s of ["waiting", "failed", "paused", "running"] as const) if (states.includes(s)) return s;
  if (states.every((s) => s === "pending")) return "pending";
  if (states.every((s) => s === "done") || laterStarted) return "done";
  return "running";
}

export function Stepper({ progress, run }: { progress: GraphProgress; run: RunDetail | undefined }) {
  const started = STAGES.map((s) => s.nodes.some((n) => (progress.nodes[n]?.state ?? "pending") !== "pending"));
  const terminal = run && ["completed", "rejected", "aborted"].includes(run.status);
  return (
    <ol className="flex items-start" aria-label="Progress">
      {STAGES.map((s, i) => {
        const laterStarted = !!terminal || started.slice(i + 1).some(Boolean);
        const state = stageState(s.nodes, progress, run, laterStarted);
        const attempts = Math.max(0, ...s.nodes.map((n) => progress.nodes[n]?.runs ?? 0));
        return (
          <li key={s.id} className="flex min-w-0 flex-1 flex-col items-center" title={`${s.label}${attempts > 1 ? ` · ${attempts} attempts` : ""}`}>
            <div className="flex w-full items-center">
              <span className={cx("h-0.5 flex-1", i === 0 ? "opacity-0" : state !== "pending" ? "bg-accent-400" : "bg-slate-200 dark:bg-slate-700")} />
              <span
                className={cx(
                  "relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                  state === "done" && "bg-accent-400 text-ink-900",
                  state === "running" && "animate-pulse-ring bg-white text-ink-900 ring-2 ring-accent-400 dark:bg-slate-900",
                  state === "waiting" && "animate-pulse-amber bg-sun-400 text-sun-950",
                  state === "paused" && "bg-sun-100 text-sun-800 dark:bg-sun-400/20 dark:text-sun-200",
                  state === "failed" && "bg-rose-600 text-white",
                  state === "pending" && "bg-white text-slate-400 ring-2 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700",
                )}
              >
                {state === "done" ? (
                  <Check className="h-4 w-4" strokeWidth={3} />
                ) : state === "failed" ? (
                  <X className="h-4 w-4" strokeWidth={3} />
                ) : state === "waiting" ? (
                  <Hand className="h-4 w-4" />
                ) : state === "paused" ? (
                  <Pause className="h-4 w-4" />
                ) : state === "running" ? (
                  <span className="h-2.5 w-2.5 rounded-full bg-accent-500" />
                ) : (
                  i + 1
                )}
              </span>
              <span className={cx("h-0.5 flex-1", i === STAGES.length - 1 ? "opacity-0" : state === "done" ? "bg-accent-400" : "bg-slate-200 dark:bg-slate-700")} />
            </div>
            <span className={cx("mt-1.5 text-center text-xs font-medium sm:text-sm", state === "pending" ? "text-slate-400 dark:text-slate-500" : "text-slate-800 dark:text-slate-200")}>
              {s.label}
            </span>
            {attempts > 1 && <span className="text-[11px] text-slate-500 dark:text-slate-400">{attempts} tries</span>}
          </li>
        );
      })}
    </ol>
  );
}
