import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { parseTs } from "../lib/format";
import type { RunDetail, RunSummary } from "../types";
import { useAsync } from "./useAsync";
import { useRunStream, type RunStream } from "./useRunStream";

const TERMINAL = new Set(["completed", "rejected", "aborted"]);

export interface LiveRun {
  run: RunDetail | undefined;
  stream: RunStream;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
  onResumed: (s: RunSummary) => void;
}

/** Run detail + live event stream, kept in sync: the detail is refetched whenever the stream reports progress. */
export function useLiveRun(id: string | undefined): LiveRun {
  const detail = useAsync<RunDetail | undefined>(() => (id ? api.run(id) : Promise.resolve(undefined)), [id]);
  const terminal = detail.data ? TERMINAL.has(detail.data.status) : false;
  const stream = useRunStream(id, terminal);
  const [override, setOverride] = useState<RunSummary | null>(null);

  const { reload } = detail;
  useEffect(() => {
    if (!stream.version) return;
    const t = window.setTimeout(reload, 250);
    return () => window.clearTimeout(t);
  }, [stream.version, reload]);

  const onResumed = useCallback(
    (s: RunSummary) => {
      setOverride(s);
      reload();
    },
    [reload],
  );

  // Merge the freshest summary (SSE status event or resume response) over the detail.
  const run: RunDetail | undefined = useMemo(() => {
    const d = detail.data;
    if (!d || d.id !== id) return undefined;
    const candidates = [stream.summary, override].filter((s): s is RunSummary => !!s && s.id === d.id);
    let merged: RunDetail = d;
    for (const s of candidates) {
      if (parseTs(s.updated_at) > parseTs(merged.updated_at) || (parseTs(s.updated_at) === parseTs(merged.updated_at) && s.status !== merged.status)) {
        merged = { ...merged, ...s };
      }
    }
    return merged;
  }, [detail.data, stream.summary, override, id]);

  return { run, stream, error: detail.error, loading: detail.loading, reload, onResumed };
}
