import { ChevronRight, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useAsync } from "../../hooks/useAsync";
import type { ProviderName, RunSummary } from "../../types";
import { Button, cx, InlineAlert } from "../ui/primitives";

const DRAFT_KEY = "forgeline-requirement-draft";

function readDraft(): string {
  try {
    return localStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Gemini / Ollama segmented control with an availability dot. */
export function ProviderToggle({ value, onChange }: { value: ProviderName; onChange: (p: ProviderName) => void }) {
  const provs = useAsync(() => api.providers(), []);
  const options: { name: ProviderName; label: string }[] = [
    { name: "gemini", label: "Gemini" },
    { name: "ollama", label: "Ollama" },
  ];
  return (
    <div className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800" role="radiogroup" aria-label="AI provider">
      {options.map((o) => {
        const info = provs.data?.providers.find((p) => p.name === o.name);
        const selected = value === o.name;
        return (
          <button
            key={o.name}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(o.name)}
            title={info ? (info.available ? "Available" : "Unavailable") : undefined}
            className={cx(
              "inline-flex items-center gap-2 rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors",
              selected ? "bg-white text-ink-900 shadow-sm dark:bg-slate-700 dark:text-white" : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white",
            )}
          >
            <span className={cx("h-2 w-2 rounded-full", !info ? "bg-slate-300" : info.available ? "bg-accent-500" : "bg-rose-500")} />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function ConsoleInput({ onStarted }: { onStarted: (run: RunSummary) => void }) {
  const reqs = useAsync(() => api.requirements(), []);
  const provs = useAsync(() => api.providers(), []);
  const [text, setText] = useState(readDraft);
  const [provider, setProvider] = useState<ProviderName>("gemini");
  const [cache, setCache] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (provs.data?.default) setProvider(provs.data.default);
  }, [provs.data]);

  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, text);
    } catch {
      /* storage unavailable */
    }
  }, [text]);

  const submit = async () => {
    if (!text.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      onStarted(await api.createRun({ requirement_text: text.trim(), provider, cache }));
    } catch {
      setError("The run could not be started. Check that the service is running and try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="card p-4 sm:p-5">
      <label htmlFor="requirement" className="block text-base font-semibold text-ink-900 dark:text-white">
        What should the agents build?
      </label>
      {reqs.data && reqs.data.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-sm text-slate-500 dark:text-slate-400">Try an example:</span>
          {reqs.data.map((r) => (
            <button
              key={r.path}
              type="button"
              onClick={() => setText(r.body)}
              className="rounded-full bg-slate-100 px-2.5 py-0.5 text-sm text-slate-700 transition-colors hover:bg-accent-100 hover:text-ink-900 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-accent-400/15 dark:hover:text-accent-200"
            >
              {r.title}
            </button>
          ))}
        </div>
      )}
      <textarea
        id="requirement"
        className="input scroll-thin mt-3 min-h-[160px] resize-y text-base leading-relaxed"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
        }}
        placeholder="Describe the change in plain words, for example: Let merchants refund part of a payment."
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <ProviderToggle value={provider} onChange={setProvider} />
        <details className="group">
          <summary className="inline-flex cursor-pointer select-none items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200">
            <ChevronRight className="h-4 w-4 transition-transform group-open:rotate-90" />
            Options
          </summary>
          <label className="mt-2 flex cursor-pointer items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={cache} onChange={(e) => setCache(e.target.checked)} className="h-4 w-4 accent-[#4f46e5]" />
            Reuse saved AI answers for identical requests
          </label>
        </details>
        <Button variant="primary" size="lg" className="ml-auto min-w-[120px]" icon={<Play className="h-4 w-4" />} loading={submitting} disabled={!text.trim()} onClick={submit}>
          Run
        </Button>
      </div>
      {error && (
        <InlineAlert tone="red" className="mt-3">
          {error}
        </InlineAlert>
      )}
    </section>
  );
}
