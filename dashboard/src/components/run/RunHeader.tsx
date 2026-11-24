import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useNow } from "../../hooks/useNow";
import { formatDuration, humanize, parseTs } from "../../lib/format";
import type { RunDetail, RunSummary } from "../../types";
import { StopRun } from "./StopRun";
import { StatusBadge } from "../ui/StatusBadge";

const ACTIVE = new Set(["created", "running", "awaiting_human"]);

export function RunHeader({ run, actions, onStopped }: { run: RunDetail; actions?: ReactNode; onStopped?: (s?: RunSummary) => void }) {
  const now = useNow(1000);
  const start = parseTs(run.created_at);
  const end = ACTIVE.has(run.status) ? now : parseTs(run.updated_at);
  const elapsed = Number.isNaN(start) ? null : Math.max(0, end - start);

  return (
    <div className="space-y-3">
      <Link to="/history" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200">
        <ArrowLeft className="h-4 w-4" /> History
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="break-words text-2xl font-semibold text-ink-900 dark:text-white">{run.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-600 dark:text-slate-400">
            <StatusBadge status={run.status} stalled={run.stalled} outcome={run.outcome} />
            <StopRun runId={run.id} status={run.status} onStopped={onStopped} />
            <span>{humanize(run.provider)}</span>
            {elapsed != null && <span className="tabular-nums">{formatDuration(elapsed)}</span>}
          </div>
          <p className="mt-1 font-mono text-xs text-slate-400">{run.id}</p>
        </div>
        {actions}
      </div>
    </div>
  );
}
