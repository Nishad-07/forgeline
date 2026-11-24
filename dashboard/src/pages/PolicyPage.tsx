import type { ReactNode } from "react";
import { api } from "../api/client";
import { ErrorState, Skeleton } from "../components/ui/primitives";
import { useAsync } from "../hooks/useAsync";
import { humanize } from "../lib/format";

type Obj = Record<string, unknown>;
const asObj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

const LIMIT_LABEL: Record<string, string> = {
  max_dev_attempts: "Attempts to make the tests pass",
  max_reconcile_rounds: "Rounds to settle a design disagreement",
  max_repair_attempts: "Retries when an AI answer is unusable",
  security_autofix_attempts: "Attempts to fix a blocking security issue",
  max_request_changes: "Change requests from a reviewer",
};

const HUMAN_LABEL: Record<string, string> = {
  clarification: "Answering questions about an unclear requirement",
  merge: "Merging a change",
  deploy: "Deploying a change",
  security_override: "Accepting a serious security risk",
  design_deadlock: "Settling a design disagreement the agents cannot resolve",
  retry_budget_exhausted: "Deciding what to do when tests keep failing",
};

function Section({ title, caption, children }: { title: string; caption: string; children: ReactNode }) {
  return (
    <section className="card p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink-900 dark:text-white">{title}</h2>
      <p className="mt-1 text-slate-500 dark:text-slate-400">{caption}</p>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-slate-100 py-2.5 last:border-0 dark:border-slate-800">
      <span className="text-slate-700 dark:text-slate-300">{label}</span>
      <span className="text-right font-semibold text-slate-900 dark:text-white">{value}</span>
    </div>
  );
}

const list = (items: unknown[]) => items.map(String).join(", ") || "None";

export function PolicyPage() {
  const res = useAsync(() => api.policy(), []);

  if (res.error && !res.data) return <ErrorState error={res.error} onRetry={res.reload} />;
  if (!res.data)
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );

  const p = res.data.policy;
  const autonomy = asObj(p.autonomy);
  const human = asArr(p.human_required_for).map(String);
  const security = asObj(p.security);
  const scope = asObj(p.scope);
  const sandbox = asObj(p.sandbox);
  const llm = asObj(p.llm);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold text-ink-900 dark:text-white">Policy</h1>
        <p className="mt-1 text-slate-500 dark:text-slate-400">The rules every run follows.</p>
      </div>

      <Section title="Limits for the agents" caption="How many times the agents may try before asking a person.">
        {Object.entries(autonomy).map(([k, v]) => (
          <Row key={k} label={LIMIT_LABEL[k] ?? humanize(k)} value={String(v)} />
        ))}
      </Section>

      <Section title="Always decided by a person" caption="The agents never do these on their own.">
        <ul className="space-y-2">
          {human.map((h) => (
            <li key={h} className="flex gap-3 text-slate-800 dark:text-slate-200">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-500" />
              {HUMAN_LABEL[h] ?? humanize(h)}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Safety rules" caption="Checked automatically on every change.">
        <Row label="Files the agents may not change" value={<span className="font-mono text-sm font-medium">{list(asArr(scope.protected_paths))}</span>} />
        {scope.max_files_changed != null && <Row label="Most files changed at once" value={String(scope.max_files_changed)} />}
        <Row label="Security issues that block a release" value={humanize(list(asArr(security.block_on)))} />
        <Row label="Tools the agents may run" value={<span className="font-mono text-sm font-medium">{list(asArr(sandbox.allowed_commands))}</span>} />
        {sandbox.command_timeout_s != null && <Row label="Time limit per tool run" value={`${String(sandbox.command_timeout_s)} s`} />}
        {llm.failover != null && <Row label="Switch AI provider automatically if one fails" value={llm.failover ? "Yes" : "No"} />}
      </Section>

      <p className="text-center text-xs text-slate-400">Policy fingerprint {res.data.sha256.slice(0, 12)}</p>
    </div>
  );
}
