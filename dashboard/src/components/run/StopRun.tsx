import { Square } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { isFinished } from "../../lib/format";
import type { RunSummary } from "../../types";
import { Button } from "../ui/primitives";
import { useActor } from "./gates/shared";

/** Small "Stop" button with an inline "Stop this run? Yes / No" confirmation. Renders nothing for finished runs. */
export function StopRun({
  runId,
  status,
  onStopped,
  size = "sm",
}: {
  runId: string;
  status: string;
  /** Called after an attempt; `s` is missing when the API refused (so the caller can still refresh). */
  onStopped?: (s?: RunSummary) => void;
  size?: "sm" | "md";
}) {
  const [actor] = useActor();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!error) return;
    const t = window.setTimeout(() => setError(null), 6000);
    return () => window.clearTimeout(t);
  }, [error]);

  if (isFinished(status) && !error) return null;

  const stop = async () => {
    setBusy(true);
    try {
      const s = await api.cancelRun(runId, actor);
      setAsking(false);
      onStopped?.(s);
    } catch (e) {
      setAsking(false);
      setError(e instanceof Error ? e.message : "Could not stop this run.");
      // The run may have just finished; let the caller refresh either way.
      onStopped?.();
    } finally {
      setBusy(false);
    }
  };

    return (
    <span className="inline-flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
      {asking ? (
        <>
          <span className="text-sm text-slate-700 dark:text-slate-300">Stop this run?</span>
          <Button size="sm" variant="danger" loading={busy} onClick={stop}>
            Yes
          </Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setAsking(false)}>
            No
          </Button>
        </>
      ) : (
        !isFinished(status) && (
          <Button size={size} variant="secondary" icon={<Square className="h-3.5 w-3.5" />} onClick={() => setAsking(true)}>
            Stop
          </Button>
        )
      )}
      {error && (
        <span role="alert" className="text-sm text-rose-700 dark:text-rose-300">
          {error}
        </span>
      )}
    </span>
  );
}
