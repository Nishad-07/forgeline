// Formatted renderers for each agent's structured output (agent_output payloads / run state).

import { AlertTriangle, ArrowRight, FilePlus2, FilePen, Lock, MessageSquareWarning } from "lucide-react";
import type { ReactNode } from "react";
import { humanize } from "../../lib/format";
import type { Finding, Severity } from "../../types";
import { Badge, BulletList, cx, ProgressBar, type Tone } from "../ui/primitives";
import { CodeBlock } from "./CodeBlock";

// ------------------------------------------------------------------ defensive accessors

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown): string => (v == null ? "" : typeof v === "string" ? v : String(v));
const strs = (v: unknown): string[] => arr(v).map(str).filter(Boolean);
const num = (v: unknown): number | null => (typeof v === "number" && !Number.isNaN(v) ? v : null);

// ------------------------------------------------------------------ building blocks

export function Section({ title, children, right, className }: { title: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={cx("min-w-0", className)}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h5 className="label">{title}</h5>
        {right}
      </div>
      {children}
    </section>
  );
}

function Prose({ children }: { children: ReactNode }) {
  return <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700 dark:text-slate-300">{children}</p>;
}

function IdChip({ children, tone = "ink" }: { children: ReactNode; tone?: "ink" | "amber" }) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center self-start whitespace-nowrap rounded-md px-1.5 py-0.5 font-mono text-[11px] font-semibold",
        tone === "ink" ? "bg-ink-900 text-accent-300 dark:bg-accent-400/15 dark:text-accent-300" : "bg-sun-100 text-sun-800 dark:bg-sun-400/15 dark:text-sun-300",
      )}
    >
      {children}
    </span>
  );
}

const SEVERITY_TONE: Record<string, Tone> = { critical: "red", high: "red", medium: "amber", low: "gray" };

// ------------------------------------------------------------------ analyst

export function AnalystOutput({ output }: { output: unknown }) {
  if (!isObj(output)) return <RawJson value={output} />;
  const criteria = arr(output.acceptance_criteria).filter(isObj);
  const tasks = arr(output.tasks).filter(isObj);
  const questions = strs(output.clarifying_questions);
  const assumptions = strs(output.assumptions);
  const outOfScope = strs(output.out_of_scope);
  const ambiguity = num(output.ambiguity_score);
  return (
    <div className="space-y-5">
      {str(output.summary) && <Prose>{str(output.summary)}</Prose>}
      {criteria.length > 0 && (
        <Section title={`Acceptance criteria · ${criteria.length}`} right={ambiguity != null ? <Badge tone={ambiguity > 0.5 ? "amber" : "gray"}>Ambiguity {ambiguity.toFixed(2)}</Badge> : undefined}>
          <div className="grid gap-2.5 lg:grid-cols-2">
            {criteria.map((c, i) => (
              <div key={str(c.id) || i} className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-950/40">
                <IdChip>{str(c.id) || `AC-${i + 1}`}</IdChip>
                <dl className="mt-2 space-y-1.5 text-sm">
                  {(["given", "when", "then"] as const).map((k) => (
                    <div key={k} className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-2">
                      <dt className="pt-px text-[11px] font-semibold uppercase tracking-wider text-accent-700 dark:text-accent-400">{k}</dt>
                      <dd className="text-slate-800 dark:text-slate-200">{str(c[k])}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </Section>
      )}
      {tasks.length > 0 && (
        <Section title={`Tasks · ${tasks.length}`}>
          <ol className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {tasks.map((t, i) => (
              <li key={str(t.id) || i} className="flex gap-3 px-3 py-2.5">
                <IdChip>{str(t.id) || `T-${i + 1}`}</IdChip>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-900 dark:text-white">{str(t.title)}</p>
                  {str(t.description) && <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">{str(t.description)}</p>}
                </div>
              </li>
            ))}
          </ol>
        </Section>
      )}
      {(assumptions.length > 0 || outOfScope.length > 0) && (
        <div className="grid gap-5 md:grid-cols-2">
          {assumptions.length > 0 && (
            <Section title="Assumptions">
              <BulletList items={assumptions} />
            </Section>
          )}
          {outOfScope.length > 0 && (
            <Section title="Out of scope">
              <BulletList items={outOfScope} />
            </Section>
          )}
        </div>
      )}
      {questions.length > 0 && (
        <Section title={`Questions for a human · ${questions.length}`}>
          <ul className="space-y-2">
            {questions.map((q, i) => (
              <li key={i} className="flex gap-2.5 rounded-lg border border-sun-200 bg-sun-50 px-3 py-2 text-sm text-sun-900 dark:border-sun-400/25 dark:bg-sun-400/10 dark:text-sun-200">
                <MessageSquareWarning className="mt-0.5 h-4 w-4 shrink-0" />
                {q}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ architect

export function ArchitectOutput({ output }: { output: unknown }) {
  if (!isObj(output)) return <RawJson value={output} />;
  const files = arr(output.impacted_components).filter(isObj);
  const untouched = strs(output.untouched_components);
  const context = strs(output.context_files);
  const risks = strs(output.risks);
  return (
    <div className="space-y-5">
      {str(output.summary) && <Prose>{str(output.summary)}</Prose>}
      {files.length > 0 && (
        <Section title={`Files to change · ${files.length}`}>
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {files.map((f, i) => {
              const create = str(f.change_type) === "create";
              return (
                <li key={i} className="flex flex-col gap-1.5 px-3 py-2.5 sm:flex-row sm:items-start sm:gap-3">
                  <div className="flex min-w-0 items-center gap-2 sm:w-[42%] sm:shrink-0">
                    {create ? <FilePlus2 className="h-4 w-4 shrink-0 text-iris-500" /> : <FilePen className="h-4 w-4 shrink-0 text-accent-600" />}
                    <code className="min-w-0 break-all font-mono text-[12.5px] font-medium text-slate-900 dark:text-white">{str(f.path)}</code>
                    <Badge tone={create ? "violet" : "green"} className="!py-0 !text-[10.5px]">
                      {create ? "Create" : "Modify"}
                    </Badge>
                  </div>
                  <p className="min-w-0 text-sm text-slate-600 dark:text-slate-400">{str(f.reason)}</p>
                </li>
              );
            })}
          </ul>
        </Section>
      )}
      <div className="grid gap-5 md:grid-cols-2">
        {untouched.length > 0 && (
          <Section title="Untouched">
            <ul className="flex flex-wrap gap-1.5">
              {untouched.map((u) => (
                <li key={u} className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 font-mono text-[11.5px] text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  <Lock className="h-3 w-3 text-slate-400" />
                  {u}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {context.length > 0 && (
          <Section title="Context (read-only)">
            <ul className="flex flex-wrap gap-1.5">
              {context.map((u) => (
                <li key={u} className="rounded-md bg-slate-100 px-2 py-1 font-mono text-[11.5px] text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                  {u}
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>
      {str(output.design_notes) && (
        <Section title="Design notes">
          <Prose>{str(output.design_notes)}</Prose>
        </Section>
      )}
      {str(output.test_strategy) && (
        <Section title="Test strategy">
          <Prose>{str(output.test_strategy)}</Prose>
        </Section>
      )}
      {risks.length > 0 && (
        <Section title="Risks">
          <BulletList items={risks} tone="amber" />
        </Section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ qa

export function QAOutput({ output, path }: { output: unknown; path?: string }) {
  if (!isObj(output)) return <RawJson value={output} />;
  const covered = strs(output.covered_criteria);
  const content = str(output.content);
  return (
    <div className="space-y-4">
      {covered.length > 0 && (
        <Section title={`Criteria covered · ${covered.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {covered.map((c) => (
              <IdChip key={c}>{c}</IdChip>
            ))}
          </div>
        </Section>
      )}
      {content ? <CodeBlock code={content} title={path || "acceptance tests"} /> : <p className="text-sm text-slate-500">No test content.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ developer

function EditBlock({ label, code, tone }: { label: string; code: string; tone: "before" | "after" }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-md border border-slate-200 dark:border-slate-800">
      <div
        className={cx(
          "px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wider",
          tone === "before" ? "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" : "bg-accent-50 text-accent-800 dark:bg-accent-400/10 dark:text-accent-300",
        )}
      >
        {label}
      </div>
      <pre
        className={cx(
          "scroll-thin max-h-64 overflow-auto p-2.5 font-mono text-[12px] leading-relaxed",
          tone === "before" ? "bg-rose-50/40 text-rose-950 dark:bg-rose-950/30 dark:text-rose-100" : "bg-accent-50/40 text-ink-950 dark:bg-accent-950/40 dark:text-accent-50",
        )}
      >
        {code || <span className="italic opacity-60">(empty)</span>}
      </pre>
    </div>
  );
}

export function DeveloperOutput({ output }: { output: unknown }) {
  if (!isObj(output)) return <RawJson value={output} />;
  const files = arr(output.files).filter(isObj);
  const objection = str(output.design_objection);
  return (
    <div className="space-y-4">
      {str(output.rationale) && <Prose>{str(output.rationale)}</Prose>}
      {objection && (
        <div className="flex gap-2.5 rounded-lg border border-sun-200 bg-sun-50 px-3 py-2.5 text-sm text-sun-900 dark:border-sun-400/25 dark:bg-sun-400/10 dark:text-sun-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-semibold">Design objection</p>
            <p className="mt-0.5">{objection}</p>
          </div>
        </div>
      )}
      {files.map((f, i) => {
        const edits = arr(f.edits).filter(isObj);
        const content = str(f.content);
        return (
          <div key={i} className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <FilePen className="h-4 w-4 text-accent-600" />
              <code className="break-all font-mono text-[12.5px] font-semibold text-slate-900 dark:text-white">{str(f.path)}</code>
              <Badge tone="gray" className="!py-0 !text-[10.5px]">
                {edits.length ? `${edits.length} edit${edits.length > 1 ? "s" : ""}` : content ? "Full content" : "No changes"}
              </Badge>
            </div>
            {edits.length > 0 ? (
              <div className="space-y-2">
                {edits.map((e, j) => (
                  <div key={j} className="grid items-stretch gap-2 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
                    <EditBlock label="Search" code={str(e.find)} tone="before" />
                    <ArrowRight className="hidden h-4 w-4 self-center text-slate-400 md:block" />
                    <EditBlock label="Replace" code={str(e.replace)} tone="after" />
                  </div>
                ))}
              </div>
            ) : content ? (
              <CodeBlock code={content} title={str(f.path)} maxHeight="max-h-80" />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ security

const VERDICT_TONE: Record<string, Tone> = {
  true_positive: "red",
  confirmed: "red",
  must_fix: "red",
  blocking: "red",
  acceptable: "green",
  false_positive: "gray",
  "pre-existing": "violet",
  pre_existing: "violet",
};

export function SecurityOutput({ triage, findings }: { triage: unknown; findings?: Finding[] }) {
  const t = isObj(triage) ? triage : null;
  const assessments = t ? arr(t.assessments).filter(isObj) : [];
  const concerns = t ? arr(t.additional_concerns).filter(isObj) : [];
  return (
    <div className="space-y-4">
      {t && str(t.summary) && <Prose>{str(t.summary)}</Prose>}
      {findings && findings.length > 0 && (
        <Section title={`Static findings · ${findings.length}`}>
          <ul className="space-y-1.5">
            {findings.map((f, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800">
                <Badge tone={SEVERITY_TONE[f.severity as Severity] ?? "gray"}>{f.severity}</Badge>
                <span className="font-medium text-slate-900 dark:text-white">{f.rule}</span>
                <code className="font-mono text-[11.5px] text-slate-500">
                  {f.file}:{f.line}
                </code>
                {f.introduced && <Badge tone="amber" className="!py-0 !text-[10.5px]">Introduced</Badge>}
                <span className="w-full text-xs text-slate-600 dark:text-slate-400">{f.message}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {assessments.length > 0 && (
        <Section title="Triage verdicts">
          <ul className="space-y-1.5">
            {assessments.map((a, i) => {
              const verdict = str(a.verdict);
              return (
                <li key={i} className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Badge tone={VERDICT_TONE[verdict] ?? "blue"}>{humanize(verdict) || "Assessed"}</Badge>
                    <span className="font-medium text-slate-900 dark:text-white">{str(a.rule)}</span>
                    <code className="font-mono text-[11.5px] text-slate-500">
                      {str(a.file)}
                      {a.line != null ? `:${str(a.line)}` : ""}
                    </code>
                  </div>
                  {str(a.explanation) && <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{str(a.explanation)}</p>}
                </li>
              );
            })}
          </ul>
        </Section>
      )}
      {concerns.length > 0 && (
        <Section title="Additional concerns">
          <ul className="space-y-1.5">
            {concerns.map((c, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-sun-200 bg-sun-50/60 px-3 py-2 text-sm dark:border-sun-400/25 dark:bg-sun-400/10">
                <Badge tone={SEVERITY_TONE[str(c.severity)] ?? "amber"}>{str(c.severity) || "note"}</Badge>
                <code className="font-mono text-[11.5px] text-slate-500">
                  {str(c.file)}
                  {c.line != null ? `:${str(c.line)}` : ""}
                </code>
                <span className="w-full text-slate-700 dark:text-slate-300">{str(c.description)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {!t && !findings?.length && <p className="text-sm text-slate-500">No findings.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ evaluator

export function RubricOutput({ output }: { output: unknown }) {
  const o = isObj(output) ? output : null;
  const items = o ? arr(o.items).filter(isObj) : [];
  if (!o) return <RawJson value={output} />;
  return (
    <div className="space-y-3">
      <ul className="space-y-2.5">
        {items.map((it, i) => {
          const score = num(it.score) ?? 0;
          return (
            <li key={i}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm font-medium text-slate-900 dark:text-white">{str(it.criterion)}</span>
                <span className="font-mono text-sm font-semibold tabular-nums text-slate-900 dark:text-white">{score}/5</span>
              </div>
              <ProgressBar value={score} max={5} tone={score >= 4 ? "green" : score >= 3 ? "amber" : "red"} size="sm" className="mt-1" />
              {str(it.justification) && <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">{str(it.justification)}</p>}
            </li>
          );
        })}
      </ul>
      {str(o.overall_comment) && <Prose>{str(o.overall_comment)}</Prose>}
    </div>
  );
}

// ------------------------------------------------------------------ release

const REC_TONE: Record<string, Tone> = { approve: "green", request_changes: "amber", reject: "red" };
const EVIDENCE_TONE: Record<string, Tone> = { met: "green", not_met: "red", unverified: "amber" };

export function ReleaseOutput({ output }: { output: unknown }) {
  if (!isObj(output)) return <RawJson value={output} />;
  const rec = str(output.recommendation);
  const evidence = arr(output.criteria_evidence).filter(isObj);
  const risks = strs(output.known_risks);
  return (
    <div className="space-y-4">
      <div>
        {str(output.headline) && <p className="text-base font-semibold text-slate-900 dark:text-white">{str(output.headline)}</p>}
        {str(output.change_summary) && <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{str(output.change_summary)}</p>}
      </div>
      {rec && (
        <div className="flex flex-wrap items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950/40">
          <Badge tone={REC_TONE[rec] ?? "gray"}>{humanize(rec)}</Badge>
          <p className="min-w-0 flex-1 text-sm text-slate-700 dark:text-slate-300">{str(output.recommendation_reason)}</p>
        </div>
      )}
      {evidence.length > 0 && (
        <Section title="Criteria evidence">
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {evidence.map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                <IdChip>{str(e.criterion_id)}</IdChip>
                <Badge tone={EVIDENCE_TONE[str(e.status)] ?? "gray"}>{humanize(str(e.status))}</Badge>
                <span className="min-w-0 flex-1 break-words font-mono text-[11.5px] text-slate-600 dark:text-slate-400">{str(e.evidence)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {risks.length > 0 && (
          <Section title="Known risks">
            <BulletList items={risks} tone="amber" />
          </Section>
        )}
        {str(output.rollback_plan) && (
          <Section title="Rollback">
            <Prose>{str(output.rollback_plan)}</Prose>
          </Section>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ fallback

export function RawJson({ value }: { value: unknown }) {
  return <p className="text-sm text-slate-500">{typeof value === "string" && value.length < 400 ? value : "Details are not available for this step."}</p>;
}
