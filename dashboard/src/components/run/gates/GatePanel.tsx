import {
  AlertOctagon,
  Ban,
  Check,
  CheckCircle2,
  Hand,
  HelpCircle,
  MessageSquareWarning,
  PauseCircle,
  Play,
  RotateCcw,
  ShieldAlert,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../../api/client";
import { useAsync } from "../../../hooks/useAsync";
import { ESCALATION_LABEL, humanize, isStopped } from "../../../lib/format";
import type {
  ClarificationGate,
  EscalationGate,
  EscalationOption,
  ProviderName,
  Recommendation,
  ReleaseApprovalGate,
  RunDetail,
  RunSummary,
} from "../../../types";
import { toPercent } from "../../ui/Gauge";
import { Badge, BulletList, Button, cx, InlineAlert } from "../../ui/primitives";
import { ProviderToggle } from "../../console/ConsoleInput";
import { ActorField, ResumeError, useActor, useResume } from "./shared";

function Shell({
  tone,
  icon,
  eyebrow,
  title,
  children,
}: {
  tone: "amber" | "red" | "orange" | "blue";
  icon: ReactNode;
  eyebrow: string;
  title: ReactNode;
  children: ReactNode;
}) {
  const ring = {
    amber: "border-amber-300 dark:border-amber-500/40 shadow-[0_0_0_4px_rgba(255,170,55,0.14)]",
    red: "border-rose-300 dark:border-rose-500/40 shadow-[0_0_0_4px_rgba(204,75,75,0.10)]",
    orange: "border-orange-300 dark:border-orange-500/40 shadow-[0_0_0_4px_rgba(255,170,55,0.14)]",
    blue: "border-brand-300 dark:border-brand-500/40 shadow-[0_0_0_4px_rgba(80,211,135,0.16)]",
  }[tone];
  const head = {
    amber: "from-amber-50 dark:from-amber-500/10 text-amber-900 dark:text-amber-200",
    red: "from-rose-50 dark:from-rose-500/10 text-rose-900 dark:text-rose-200",
    orange: "from-orange-50 dark:from-orange-500/10 text-orange-900 dark:text-orange-200",
    blue: "from-brand-50 dark:from-brand-500/10 text-brand-900 dark:text-brand-200",
  }[tone];
  const iconBg = {
    amber: "bg-sun-400 text-sun-950 animate-pulse-amber",
    red: "bg-rose-600 text-white",
    orange: "bg-sun-400 text-sun-950",
    blue: "bg-accent-400 text-ink-900 animate-pulse-ring",
  }[tone];
  return (
    <section className={cx("animate-fade-in overflow-hidden rounded-xl border-2 bg-white dark:bg-slate-900", ring)} aria-live="polite">
      <header className={cx("flex items-center gap-3 bg-gradient-to-r to-transparent px-4 py-3.5 sm:px-5", head)}>
        <span className={cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", iconBg)}>{icon}</span>
        <div className="min-w-0">
          <p className="text-[11px] font-bold uppercase tracking-widest opacity-80">{eyebrow}</p>
          <h2 className="text-base font-semibold leading-tight sm:text-lg">{title}</h2>
        </div>
      </header>
      <div className="px-4 pb-4 pt-4 sm:px-5 sm:pb-5">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ clarification

function ClarificationPanel({ run, gate, onDone }: { run: RunDetail; gate: ClarificationGate; onDone: (s: RunSummary) => void }) {
  const [actor, setActor] = useActor();
  const [answers, setAnswers] = useState<string[]>(() => gate.questions.map(() => ""));
  const resume = useResume(run.id, onDone);
  const questionsKey = gate.questions.join("|");
  const questionCount = gate.questions.length;
  useEffect(() => setAnswers(Array.from({ length: questionCount }, () => "")), [questionsKey, questionCount]);
  const ready = actor.trim() && answers.every((a) => a.trim());

  return (
    <Shell tone="amber" icon={<HelpCircle className="h-5 w-5" />} eyebrow="Clarification" title="Analyst questions">
      <p className="mb-4 text-base text-slate-700 dark:text-slate-300">The analyst needs a few answers before it can plan the work.</p>
      <div className="space-y-4">
        {gate.questions.map((q, i) => (
          <div key={i} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
            <label htmlFor={`q${i}`} className="mb-2 flex gap-2 text-sm font-medium text-slate-900 dark:text-white">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[11px] font-bold text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">
                {i + 1}
              </span>
              {q}
            </label>
            <textarea
              id={`q${i}`}
              className="input min-h-[72px]"
              value={answers[i] ?? ""}
              placeholder="Your answer…"
              onChange={(e) => setAnswers((prev) => prev.map((a, j) => (j === i ? e.target.value : a)))}
            />
          </div>
        ))}
      </div>
      {gate.assumptions?.length > 0 && (
        <div className="mt-4 rounded-xl bg-slate-50 p-4 dark:bg-slate-800/50">
          <p className="label mb-2">Current assumptions</p>
          <BulletList items={gate.assumptions} />
        </div>
      )}
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="sm:w-72">
          <ActorField value={actor} onChange={setActor} />
        </div>
        <Button
          variant="primary"
          size="lg"
          icon={<Check className="h-4 w-4" />}
          loading={resume.submitting}
          disabled={!ready}
          onClick={() => resume.submit({ decision: { answers: answers.map((a) => a.trim()), actor: actor.trim() }, provider: null })}
        >
          Submit answers
        </Button>
        {!ready && <p className="text-xs text-slate-500 sm:pb-3">Answer every question and enter your name.</p>}
      </div>
      <div className="mt-3">
        <ResumeError error={resume.error} />
      </div>
    </Shell>
  );
}

// ------------------------------------------------------------------ release approval

const REC_TEXT: Record<string, string> = {
  approve: "The release agent recommends approving this change.",
  request_changes: "The release agent recommends changes before approval.",
  reject: "The release agent recommends rejecting this change.",
};

function Fact({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div>
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <p className={cx("text-xl font-semibold tabular-nums", bad ? "text-rose-700 dark:text-rose-300" : "text-slate-900 dark:text-white")}>{value}</p>
    </div>
  );
}

function ReleasePanel({ run, gate, onDone }: { run: RunDetail; gate: ReleaseApprovalGate; onDone: (s: RunSummary) => void }) {
  const [actor, setActor] = useActor();
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState<Recommendation | null>(null);
  const resume = useResume(run.id, onDone);
  const overall = toPercent(gate.scores?.overall);
  const tests = run.state.qa?.tests;
  const failed = tests ? tests.failed + tests.errors : 0;
  const blocking = Number(gate.facts?.blocking_security_findings ?? run.state.security?.blocking.length ?? 0);

  const decide = async (decision: Recommendation) => {
    setPending(decision);
    await resume.submit({ decision: { decision, comment: comment.trim(), actor: actor.trim() }, provider: null });
    setPending(null);
  };
  const needsComment = !comment.trim();

  return (
    <Shell tone="blue" icon={<Hand className="h-5 w-5" />} eyebrow="Your approval" title={gate.headline || "Ready to release"}>
      <p className="text-base text-slate-700 dark:text-slate-300">Review the result and decide whether to merge this change.</p>
      <div className={cx("mt-4 rounded-lg px-4 py-3", gate.recommendation === "approve" ? "bg-accent-50 dark:bg-accent-400/10" : gate.recommendation === "reject" ? "bg-rose-50 dark:bg-rose-500/10" : "bg-sun-50 dark:bg-sun-400/10")}>
        <p className="font-semibold text-slate-900 dark:text-white">{REC_TEXT[gate.recommendation] ?? "The release agent made a recommendation."}</p>
        {gate.reason && <p className="mt-1 text-slate-700 dark:text-slate-300">{gate.reason}</p>}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-4">
        <Fact label="Score" value={overall != null ? `${overall.toFixed(0)} / 100` : "—"} />
        <Fact label="Tests" value={tests ? (failed ? `${failed} failing` : "All passing") : "—"} bad={failed > 0} />
        <Fact label="Serious security issues" value={String(blocking)} bad={blocking > 0} />
      </div>
      {gate.known_risks.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 font-semibold text-slate-900 dark:text-white">Known risks</p>
          <BulletList items={gate.known_risks} tone="amber" />
        </div>
      )}
      <div className="mt-5 grid gap-3 md:grid-cols-[260px_minmax(0,1fr)]">
        <ActorField value={actor} onChange={setActor} />
        <label className="block">
          <span className="label mb-1.5 block">Comment {needsComment ? <span className="normal-case tracking-normal text-slate-400">(needed to request changes)</span> : null}</span>
          <textarea className="input min-h-[42px]" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Optional note, or the changes you want" />
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="success" size="lg" icon={<ThumbsUp className="h-4 w-4" />} disabled={!actor.trim() || resume.submitting} loading={pending === "approve"} onClick={() => decide("approve")}>
          Approve and merge
        </Button>
        <Button
          variant="warning"
          size="lg"
          icon={<RotateCcw className="h-4 w-4" />}
          disabled={!actor.trim() || needsComment || resume.submitting}
          loading={pending === "request_changes"}
          onClick={() => decide("request_changes")}
          title={needsComment ? "Add a comment describing the changes" : undefined}
        >
          Request changes
        </Button>
        <Button variant="danger" size="lg" icon={<ThumbsDown className="h-4 w-4" />} disabled={!actor.trim() || resume.submitting} loading={pending === "reject"} onClick={() => decide("reject")}>
          Reject
        </Button>
      </div>
      {!actor.trim() && <p className="mt-2 text-sm text-slate-500">Enter your name to decide.</p>}
      <div className="mt-3">
        <ResumeError error={resume.error} />
      </div>
    </Shell>
  );
}

// ------------------------------------------------------------------ escalation

const OPTION_META: Record<EscalationOption, { label: string; variant: "primary" | "warning" | "danger"; icon: ReactNode; help: string }> = {
  retry: { label: "Retry with guidance", variant: "primary", icon: <RotateCcw className="h-4 w-4" />, help: "New attempt budget; comment is sent as guidance." },
  override: { label: "Override", variant: "warning", icon: <ShieldAlert className="h-4 w-4" />, help: "Accept the risk and continue. Justification required." },
  abort: { label: "Abort run", variant: "danger", icon: <Ban className="h-4 w-4" />, help: "Stop the run. Nothing is merged." },
};

function EscalationPanel({ run, gate, onDone }: { run: RunDetail; gate: EscalationGate; onDone: (s: RunSummary) => void }) {
  const [actor, setActor] = useActor();
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState<EscalationOption | null>(null);
  const resume = useResume(run.id, onDone);
  const blocking = gate.reason === "security_block" ? run.state.security?.blocking ?? [] : [];

  const decide = async (decision: EscalationOption) => {
    setPending(decision);
    await resume.submit({ decision: { decision, comment: comment.trim(), actor: actor.trim() }, provider: null });
    setPending(null);
  };

  return (
    <Shell tone="red" icon={<AlertOctagon className="h-5 w-5" />} eyebrow="Needs your decision" title={ESCALATION_LABEL[gate.reason] ?? humanize(gate.reason)}>
      <p className="mb-3 text-base text-slate-700 dark:text-slate-300">The agents could not finish this step on their own. Choose how to continue.</p>
      <p className="whitespace-pre-wrap rounded-xl bg-slate-50 p-4 text-sm leading-relaxed text-slate-700 dark:bg-slate-800/50 dark:text-slate-300">{gate.details}</p>
      {blocking.length > 0 && (
        <div className="mt-3 space-y-2">
          {blocking.map((f, i) => (
            <div key={i} className="rounded-xl border border-rose-200 bg-rose-50/60 p-3 dark:border-rose-500/30 dark:bg-rose-500/10">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-rose-900 dark:text-rose-200">
                <Badge tone="red">{f.severity}</Badge>
                {f.rule}
                <span className="font-mono text-xs text-rose-700/80 dark:text-rose-300/80">
                  {f.file}:{f.line}
                </span>
              </p>
              <p className="mt-1 text-sm text-rose-800 dark:text-rose-200/90">{f.message}</p>
              {f.evidence && <pre className="scroll-thin mt-2 overflow-x-auto rounded-lg bg-slate-900 p-2.5 font-mono text-xs text-rose-200">{f.evidence}</pre>}
            </div>
          ))}
        </div>
      )}
      <div className="mt-4 grid gap-3 md:grid-cols-[260px_minmax(0,1fr)]">
        <ActorField value={actor} onChange={setActor} />
        <label className="block">
          <span className="label mb-1.5 block">
            Comment / justification{" "}
            {gate.options.includes("override") && <span className="normal-case tracking-normal text-slate-400">(required for override)</span>}
          </span>
          <textarea className="input min-h-[42px]" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Guidance for a retry, or justification for an override" />
        </label>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {gate.options.map((opt) => {
          const meta = OPTION_META[opt];
          const disabled = !actor.trim() || resume.submitting || (opt === "override" && !comment.trim());
          return (
            <div key={opt} className="flex flex-col gap-1">
              <Button variant={meta.variant} size="lg" icon={meta.icon} disabled={disabled} loading={pending === opt} onClick={() => decide(opt)}>
                {meta.label}
              </Button>
              <p className="px-1 text-[11px] leading-snug text-slate-500 dark:text-slate-400">{meta.help}</p>
            </div>
          );
        })}
      </div>
      {!actor.trim() && <p className="mt-2 text-xs text-slate-500">Enter your name to decide.</p>}
      <div className="mt-3">
        <ResumeError error={resume.error} />
      </div>
    </Shell>
  );
}

// ------------------------------------------------------------------ paused / failed

function ResumePanel({ run, onDone }: { run: RunDetail; onDone: (s: RunSummary) => void }) {
  const providers = useAsync(() => api.providers(), []);
  const [provider, setProvider] = useState<ProviderName>(run.provider);
  const resume = useResume(run.id, onDone);
  const paused = run.status === "paused_provider_unavailable";
  useEffect(() => {
    // Suggest the other provider when the current one is down.
    const cur = providers.data?.providers.find((p) => p.name === run.provider);
    const other = providers.data?.providers.find((p) => p.name !== run.provider && p.available);
    if (cur && !cur.available && other) setProvider(other.name);
  }, [providers.data, run.provider]);

  return (
    <Shell
      tone={paused ? "orange" : "red"}
      icon={paused ? <PauseCircle className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
      eyebrow={paused ? "Paused" : "Stopped"}
      title={paused ? "The AI provider is unavailable" : "The run stopped with an error"}
    >
      <p className="mb-2 text-sm text-slate-600 dark:text-slate-300">
        {paused
          ? "Resume from the last completed step."
          : "Resume from the last completed step, optionally on another provider."}
      </p>
      <p className="mb-2 text-sm text-slate-600 dark:text-slate-400">Resume with</p>
      <ProviderToggle value={provider} onChange={setProvider} />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="primary" size="lg" icon={<Play className="h-4 w-4" />} loading={resume.submitting} onClick={() => resume.submit({ decision: null, provider })}>
          Resume run{provider !== run.provider ? ` on ${provider === "ollama" ? "Ollama" : "Gemini"}` : ""}
        </Button>
        {provider !== run.provider && (
          <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
            <TriangleAlert className="h-3.5 w-3.5 text-amber-500" />
            Switches the run from {run.provider} to {provider}
          </span>
        )}
      </div>
      <div className="mt-3">
        <ResumeError error={resume.error} />
      </div>
    </Shell>
  );
}

// ------------------------------------------------------------------ dispatcher

export function GatePanel({ run, onDone }: { run: RunDetail; onDone: (s: RunSummary) => void }) {
  if (run.status === "paused_provider_unavailable" || run.status === "failed") return <ResumePanel run={run} onDone={onDone} />;
  if (run.status !== "awaiting_human") return null;
  const gate = run.gate;
  if (!gate)
    return (
      <InlineAlert tone="amber" icon={<MessageSquareWarning className="h-4 w-4" />} title="Waiting for a human">
        Loading gate details…
      </InlineAlert>
    );
  if (gate.gate === "clarification") return <ClarificationPanel key={run.id + gate.questions.join("|")} run={run} gate={gate} onDone={onDone} />;
  if (gate.gate === "release_approval") return <ReleasePanel key={run.id + gate.commit} run={run} gate={gate} onDone={onDone} />;
  if (gate.gate === "escalation") return <EscalationPanel key={run.id + gate.reason + gate.details} run={run} gate={gate} onDone={onDone} />;
  return null;
}

export function OutcomeBanner({ run }: { run: RunDetail }) {
  if (run.status === "completed")
    return (
      <InlineAlert tone="green" icon={<CheckCircle2 className="h-4 w-4" />} title={run.outcome === "merged" ? "Merged and deployed" : "Completed"}>
        {(() => {
          const d = [...(run.state.human_decisions ?? [])].reverse().find((h) => h.gate === "release_approval");
          return d ? `Approved by ${d.actor}${d.comment ? `: “${d.comment}”` : ""}` : "The run finished successfully.";
        })()}
      </InlineAlert>
    );
  if (isStopped(run))
    return (
      <InlineAlert tone="amber" icon={<Ban className="h-4 w-4" />} title="Run stopped">
        Nothing was merged.
      </InlineAlert>
    );
  if (run.status === "rejected" || run.status === "aborted")
    return (
      <InlineAlert tone="amber" icon={<Ban className="h-4 w-4" />} title={run.status === "rejected" ? "Release rejected" : "Run aborted"}>
        Nothing was merged.
      </InlineAlert>
    );
  return null;
}
