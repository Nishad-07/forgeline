import { Package } from "lucide-react";
import { isStopped } from "../../lib/format";
import type { RunDetail } from "../../types";
import { toPercent } from "../ui/Gauge";
import { Card } from "../ui/primitives";

function outcomeText(run: RunDetail): string {
  if (run.status === "completed") {
    const d = [...(run.state.human_decisions ?? [])].reverse().find((h) => h.gate === "release_approval");
    return d?.actor ? `Merged to main by ${d.actor}` : "Merged to main";
  }
  if (run.status === "rejected") return "Rejected";
  if (run.status === "aborted") return isStopped(run) ? "Stopped" : "Aborted";
  return "";
}

function stepsDone(run: RunDetail): string {
  const s = run.state;
  const done = [s.analysis && "plan", s.design && "design", s.acceptance && "acceptance tests", s.qa && "code and checks", s.security && "security review"].filter(Boolean);
  return done.length ? done.join(", ") : "no steps";
}

/** Short plain-language summary of what a run produced. */
export function WhatWasBuilt({ run }: { run: RunDetail }) {
  const s = run.state;
  const rel = s.release;
  const outcome = outcomeText(run);

  if (!rel) {
    return (
      <Card title="What was built" icon={<Package className="h-4 w-4" />}>
        <p className="font-medium text-slate-900 dark:text-white">Stopped before release</p>
        <p className="mt-1 text-slate-600 dark:text-slate-400">Steps completed: {stepsDone(run)}.</p>
        {outcome && outcome !== "Stopped" && <p className="mt-1 text-slate-600 dark:text-slate-400">Outcome: {outcome}</p>}
      </Card>
    );
  }

  const files = s.evaluation?.agent_files?.length ?? rel.diff_stat?.files;
  const facts: string[] = [];
  if (files != null) {
    const st = rel.diff_stat;
    facts.push(`${files} ${files === 1 ? "file" : "files"} changed${st && (st.added || st.removed) ? ` (+${st.added} -${st.removed})` : ""}`);
  }
  const t = s.qa?.tests;
  if (t) facts.push(`Tests: ${t.passed}/${t.total} passed`);
  if (t?.coverage_percent != null) facts.push(`Coverage: ${t.coverage_percent}%`);
  if (s.security) {
    const n = s.security.findings.length;
    facts.push(`Security: ${n} ${n === 1 ? "finding" : "findings"}`);
  }
  if (s.evaluation) {
    const score = toPercent(s.evaluation.scores.overall);
    facts.push(s.evaluation.golden.available ? `Score: ${score != null ? score.toFixed(0) : "n/a"}/100` : score != null ? `Score: ${score.toFixed(0)}/100 (rubric only)` : "Score: rubric only");
  }
  const attempts = s.dev_attempt ?? s.attempts_log?.length;
  if (attempts) facts.push(`${attempts} ${attempts === 1 ? "attempt" : "attempts"}`);

  return (
    <Card title="What was built" icon={<Package className="h-4 w-4" />}>
      <p className="font-semibold text-slate-900 dark:text-white">{rel.headline}</p>
      {rel.change_summary && <p className="mt-1 text-slate-700 dark:text-slate-300">{rel.change_summary}</p>}
      {facts.length > 0 && <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">{facts.join(" · ")}</p>}
      {outcome && <p className="mt-1 text-sm font-medium text-ink-900 dark:text-accent-300">{outcome}</p>}
    </Card>
  );
}
