import { Plus } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Button, EmptyState, ErrorState, Skeleton } from "../components/ui/primitives";
import { ProviderBadge, StatusBadge } from "../components/ui/StatusBadge";
import { useAsync } from "../hooks/useAsync";
import { useNow } from "../hooks/useNow";
import { StopRun } from "../components/run/StopRun";
import { absoluteTime, isFinished, relativeTime } from "../lib/format";

/** Finished runs open on their detail tabs; everything else opens in the live console. */
function runPath(r: { id: string; status: string }): string {
  return isFinished(r.status) ? `/runs/${r.id}?view=details` : `/runs/${r.id}`;
}

export function HistoryPage() {
  const navigate = useNavigate();
  const runs = useAsync(() => api.runs(), []);
  const now = useNow(10_000);

  useEffect(() => {
    const t = window.setInterval(runs.reload, 5000);
    return () => window.clearInterval(t);
  }, [runs.reload]);

  const list = runs.data ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-ink-900 dark:text-white">History</h1>
        <Link to="/">
          <Button variant="primary" icon={<Plus className="h-4 w-4" />}>
            New requirement
          </Button>
        </Link>
      </div>

      <div className="card overflow-hidden">
        {runs.error && !runs.data ? (
          <div className="p-5">
            <ErrorState error={runs.error} onRetry={runs.reload} />
          </div>
        ) : !runs.data ? (
          <div className="space-y-3 p-5">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : list.length === 0 ? (
          <EmptyState title="No runs yet" description="Runs you start appear here." />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-200 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
                  <th className="px-4 py-3 font-medium">Requirement</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="hidden px-4 py-3 font-medium sm:table-cell">Provider</th>
                  <th className="px-4 py-3 font-medium">Started</th>
                  <th className="px-4 py-3 font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => navigate(runPath(r))}
                    className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-accent-50/60 dark:border-slate-800 dark:hover:bg-slate-800/40"
                  >
                    <td className="px-4 py-3">
                      <Link to={runPath(r)} onClick={(e) => e.stopPropagation()} className="font-medium text-slate-900 hover:text-accent-700 dark:text-white dark:hover:text-accent-300">
                        {r.title}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={r.status} stalled={r.stalled} outcome={r.outcome} />
                    </td>
                    <td className="hidden px-4 py-3 sm:table-cell">
                      <ProviderBadge provider={r.provider} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400" title={absoluteTime(r.created_at)}>
                      {relativeTime(r.created_at, now)}
                    </td>
                    <td className="px-4 py-3">
                      <StopRun runId={r.id} status={r.status} onStopped={runs.reload} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
