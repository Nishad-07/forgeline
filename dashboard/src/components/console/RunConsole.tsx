import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LiveRun } from "../../hooks/useLiveRun";
import { buildFeed, parseTestSummary } from "../../lib/feed";
import { deriveProgress } from "../../lib/nodeStatus";
import { EmptyState, Skeleton } from "../ui/primitives";
import { GateItem, HaltItem, NoticeRow, OutcomeItem, StepCard, type CardSnapshot } from "./FeedItems";
import { isFinished } from "../../lib/format";
import { WhatWasBuilt } from "../run/WhatWasBuilt";
import { Stepper } from "./Stepper";

const ACTIVE = new Set(["created", "running", "awaiting_human", "paused_provider_unavailable", "failed"]);

/**
 * Keeps the newest activity in view. Following pauses as soon as the reader scrolls up
 * and resumes when they scroll back to the bottom.
 */
function useAutoFollow(enabled: boolean, deps: unknown[]) {
  const paused = useRef(false);
  useEffect(() => {
    let lastY = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const atBottom = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
      if (atBottom) paused.current = false;
      else if (y < lastY - 4) paused.current = true;
      lastY = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (!enabled || paused.current) return;
    window.scrollTo({ top: document.documentElement.scrollHeight });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);
}

/** Live view of one run: a progress bar and one collapsible card per agent step. */
export function RunConsole({ live }: { live: LiveRun }) {
  const { run, stream, onResumed } = live;
  const snapshots = useRef(new Map<string, CardSnapshot>());
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  const progress = useMemo(() => deriveProgress(stream.events, run ?? null, run?.next ?? []), [stream.events, run]);
  const feed = useMemo(() => buildFeed(stream.events, stream.calls, run), [stream.events, stream.calls, run]);
  const callMap = useMemo(() => new Map(stream.calls.map((c) => [c.call, c])), [stream.calls]);
  const toggle = useCallback((key: string, open: boolean) => setToggled((t) => ({ ...t, [key]: !open })), []);

  // Freeze each card's run-state slice while it is the latest execution of its step,
  // so earlier attempts keep their own results after a retry.
  useEffect(() => {
    if (!run) return;
    for (const f of feed) {
      if (f.type !== "card" || !f.isLatest || f.status !== "done") continue;
      const s = run.state;
      if (f.node === "qa_run") {
        const sum = parseTestSummary(f.summary);
        if (s.qa && (sum.passed == null || s.qa.tests.passed === sum.passed)) snapshots.current.set(f.key, { qa: s.qa });
      } else if (f.node === "apply_change") snapshots.current.set(f.key, { scope: s.scope });
      else if (f.node === "security") snapshots.current.set(f.key, { security: s.security });
      else if (f.node === "evaluate") snapshots.current.set(f.key, { evaluation: s.evaluation });
    }
  }, [feed, run]);

  useEffect(() => {
    snapshots.current.clear();
    setToggled({});
  }, [run?.id]);

  const streamLength = stream.calls.reduce((n, c) => n + c.text.length, 0);
  useAutoFollow(!!run && ACTIVE.has(run.status), [feed.length, streamLength, run?.status]);

  if (!run) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  const running = run.status === "running" || run.status === "created";

  return (
    <div className="min-w-0 space-y-4">
      <section className="card px-3 py-4 sm:px-5">
        <Stepper progress={progress} run={run} />
      </section>

      <ol className="space-y-2.5">
        {feed.map((f) => (
          <li key={f.key}>
            {f.type === "card" ? (
              (() => {
                const open = toggled[f.key] ?? (f.status === "running" || f.status === "paused" || f.status === "failed");
                return (
                  <StepCard card={f} calls={callMap} run={run} snap={snapshots.current.get(f.key)} open={open} onToggle={() => toggle(f.key, open)} />
                );
              })()
            ) : f.type === "gate" ? (
              <GateItem item={f} run={run} onResumed={onResumed} />
            ) : f.type === "halt" ? (
              <HaltItem item={f} run={run} onResumed={onResumed} />
            ) : f.type === "notice" ? (
              <NoticeRow event={f.event} />
            ) : (
              <OutcomeItem item={f} run={run} />
            )}
          </li>
        ))}
        {feed.length === 0 && (
          <li className="card">
            <EmptyState title={running ? "Starting the agents" : "Nothing to show yet"} className="py-8" />
          </li>
        )}
      </ol>
      {isFinished(run.status) && <WhatWasBuilt run={run} />}
    </div>
  );
}
