import { Plus } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { ConsoleInput } from "../components/console/ConsoleInput";
import { RunConsole } from "../components/console/RunConsole";
import { Button, ErrorState } from "../components/ui/primitives";
import { StopRun } from "../components/run/StopRun";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useLiveRun } from "../hooks/useLiveRun";

export function ConsolePage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run") ?? undefined;
  const live = useLiveRun(runId);

  const setRun = (id: string | null) =>
    setParams((p) => {
      const next = new URLSearchParams(p);
      if (id) next.set("run", id);
      else next.delete("run");
      return next;
    });

  if (!runId)
    return (
      <div className="mx-auto max-w-3xl space-y-5">
        <div>
          <h1 className="text-2xl font-semibold text-ink-900 dark:text-white">Agent Console</h1>
          <p className="mt-1 text-slate-500 dark:text-slate-400">Describe a change. Seven AI agents plan, build and check it, and you approve the result.</p>
        </div>
        <ConsoleInput onStarted={(r) => setRun(r.id)} />
      </div>
    );

  const run = live.run;
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="card flex flex-wrap items-center gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-semibold text-ink-900 dark:text-white">{run?.title ?? "Starting…"}</p>
        </div>
        {run && <StatusBadge status={run.status} stalled={run.stalled} outcome={run.outcome} />}
        {run && <StopRun runId={run.id} status={run.status} onStopped={(s) => (s ? live.onResumed(s) : live.reload())} />}
        <Link to={`/runs/${runId}?view=details`} className="text-sm font-medium text-accent-700 hover:underline dark:text-accent-400">
          Details
        </Link>
        <Button variant="secondary" size="sm" icon={<Plus className="h-4 w-4" />} onClick={() => setRun(null)}>
          New requirement
        </Button>
      </div>
      {live.error && !run ? <ErrorState error="This run could not be loaded." onRetry={live.reload} /> : <RunConsole live={live} />}
    </div>
  );
}
