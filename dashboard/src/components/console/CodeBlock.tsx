import { Check, Copy } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../ui/primitives";

const KEYWORDS = new Set(
  "def class return import from as assert if elif else for while in not and or is with try except finally raise pass break continue lambda yield async await None True False self global nonlocal del".split(
    " ",
  ),
);

const TOKEN = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(@[\w.]+)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)(?=\s*\()|\b([A-Za-z_]\w*)\b/g;

/** Minimal Python-flavoured highlighter: comments, strings, keywords, numbers, decorators, calls. */
export function highlight(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN.exec(code))) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const [text, com, str, dec, num, fn, word] = m;
    let cls: string | null = null;
    if (com) cls = "tok-com";
    else if (str) cls = "tok-str";
    else if (dec) cls = "tok-dec";
    else if (num) cls = "tok-num";
    else if (fn) cls = KEYWORDS.has(fn) ? "tok-kw" : "tok-fn";
    else if (word && KEYWORDS.has(word)) cls = "tok-kw";
    out.push(cls ? (
      <span key={k++} className={cls}>
        {text}
      </span>
    ) : (
      text
    ));
    last = m.index + text.length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        });
      }}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-white/10 hover:text-white"
      aria-label="Copy code"
    >
      {done ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export function CodeBlock({
  code,
  title,
  className,
  maxHeight = "max-h-[420px]",
  plain = false,
}: {
  code: string;
  title?: ReactNode;
  className?: string;
  maxHeight?: string;
  plain?: boolean;
}) {
  const nodes = useMemo(() => (plain ? [code] : highlight(code)), [code, plain]);
  return (
    <div className={cx("overflow-hidden rounded-lg border border-ink-800 bg-ink-950 dark:border-slate-800", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-1.5">
        <span className="min-w-0 truncate font-mono text-[11px] text-slate-300">{title}</span>
        <CopyButton text={code} />
      </div>
      <pre className={cx("scroll-thin overflow-auto p-3 font-mono text-[12px] leading-relaxed text-slate-100", maxHeight)}>
        <code>{nodes}</code>
      </pre>
    </div>
  );
}

/** Live token stream: monospace, auto-scrolls while the reader is at the bottom, blinking cursor while active. */
export function StreamView({ text, active, className }: { text: string; active: boolean; className?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <pre
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      className={cx(
        "scroll-thin max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-ink-800 bg-ink-950 p-3 font-mono text-[12px] leading-relaxed text-accent-100 dark:border-slate-800",
        className,
      )}
      aria-live="off"
    >
      {text || <span className="text-slate-400">Waiting for output</span>}
      {active && <span className="stream-cursor" aria-hidden />}
    </pre>
  );
}
