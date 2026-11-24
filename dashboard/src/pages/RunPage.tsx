import { useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { RunConsole } from "../components/console/RunConsole";
import { RunDetails } from "../components/details/RunDetails";
import { GatePanel, OutcomeBanner } from "../components/run/gates/GatePanel";
import { RunHeader } from "../components/run/RunHeader";
import { Button, Card, cx, EmptyState, ErrorState, Skeleton } from "../components/ui/primitives";
import { useLiveRun } from "../hooks/useLiveRun";
import { isFinished } from "../lib/format";

type View = "console" | "details";

function ViewSwitch({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div className="inline-flex rounded-lg bg-slate-200/70 p-1 dark:bg-slate-800" role="tablist" aria-label="View">
      {(["console", "details"] as const).map((v) => (
        <button
          key={v}
          role="tab"
          aria-selected={view === v}
          onClick={() => onChange(v)}
          className={cx(
            "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
            view === v ? "bg-white text-ink-900 shadow-sm dark:bg-slate-700 dark:text-white" : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white",
          )}
        >
          {v === "console" ? "Live" : "Details"}
        </button>
      ))}
    </div>
  );
}

export function RunPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const update = (k: string, v: string) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.set(k, v);
        return next;
      },
      { replace: true },
    );

  const live = useLiveRun(id);
  const { run, stream, onResumed } = live;

  // With no explicit view, a run that was already finished when opened starts on its detail tabs.
  const openedFinished = useRef<boolean | null>(null);
  if (openedFinished.current === null && run) openedFinished.current = isFinished(run.status);
  const requested = params.get("view");
  const view: View = requested === "details" || requested === "console" ? requested : openedFinished.current ? "details" : "console";

  if (live.error && !run) {
    const notFound = live.error instanceof ApiError && live.error.status === 404;
    return (
      <div className="mx-auto max-w-xl pt-10">
        {notFound ? (
          <Card>
            <EmptyState
              title="Run not found"
              action={
                <Link to="/history">
                  <Button variant="primary">Go to history</Button>
                </Link>
              }
            />
          </Card>
        ) : (
          <ErrorState error="This run could not be loaded." onRetry={live.reload} />
        )}
      </div>
    );
  }

  if (!run)
    return (
      <div className="mx-auto max-w-5xl space-y-5">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-[320px] w-full" />
      </div>
    );

  return (
    <div className={cx("mx-auto space-y-5", view === "console" ? "max-w-4xl" : "max-w-5xl")}>
      <RunHeader
        run={run}
        onStopped={(s) => (s ? onResumed(s) : live.reload())}
        actions={
          isFinished(run.status) ? (
            <button
              onClick={() => update("view", view === "details" ? "console" : "details")}
              className="text-sm font-medium text-accent-700 hover:underline dark:text-accent-400"
            >
              {view === "details" ? "View as activity feed" : "View details"}
            </button>
          ) : (
            <ViewSwitch view={view} onChange={(v) => update("view", v)} />
          )
        }
      />
      {view === "console" ? (
        <RunConsole live={live} />
      ) : (
        <>
          <GatePanel run={run} onDone={onResumed} />
          <OutcomeBanner run={run} />
          <RunDetails run={run} stream={stream} tab={params.get("tab") ?? "overview"} onTab={(t) => update("tab", t)} />
        </>
      )}
    </div>
  );
}
