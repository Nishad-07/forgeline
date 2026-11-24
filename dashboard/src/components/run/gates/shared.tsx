import { ShieldAlert, UserRound } from "lucide-react";
import { useCallback, useState } from "react";
import { api, ApiError } from "../../../api/client";
import type { ResumeBody, RunSummary } from "../../../types";
import { InlineAlert } from "../../ui/primitives";

const ACTOR_KEY = "forgeline-actor";

export function useActor(): [string, (v: string) => void] {
  const [actor, setActorState] = useState<string>(() => {
    try {
      return localStorage.getItem(ACTOR_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const setActor = useCallback((v: string) => {
    setActorState(v);
    try {
      localStorage.setItem(ACTOR_KEY, v);
    } catch {
      /* ignore */
    }
  }, []);
  return [actor, setActor];
}

export interface ResumeState {
  submit: (body: ResumeBody) => Promise<boolean>;
  submitting: boolean;
  error: { status: number; message: string } | null;
  clearError: () => void;
}

export function useResume(runId: string, onDone: (s: RunSummary) => void): ResumeState {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const submit = useCallback(
    async (body: ResumeBody) => {
      setSubmitting(true);
      setError(null);
      try {
        const s = await api.resume(runId, body);
        onDone(s);
        return true;
      } catch (e) {
        if (e instanceof ApiError) setError({ status: e.status, message: e.detail || e.message });
        else setError({ status: 0, message: e instanceof Error ? e.message : String(e) });
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [runId, onDone],
  );
  return { submit, submitting, error, clearError: () => setError(null) };
}

export function ResumeError({ error }: { error: ResumeState["error"] }) {
  if (!error) return null;
  return (
    <InlineAlert
      tone="red"
      icon={<ShieldAlert className="h-4 w-4" />}
      title={error.status === 409 ? "This decision could not be applied" : "The request failed"}
    >
      {error.message}
    </InlineAlert>
  );
}

export function ActorField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="label mb-1.5 flex items-center gap-1.5">
        <UserRound className="h-3.5 w-3.5" /> Your name
      </span>
      <input className="input" value={value} onChange={(e) => onChange(e.target.value)} placeholder="e.g. Priya Sharma" autoComplete="name" />
    </label>
  );
}
