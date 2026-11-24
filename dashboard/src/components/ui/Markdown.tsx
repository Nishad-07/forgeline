import { Fragment, type ReactNode } from "react";
import { cx } from "./primitives";

/** Minimal, safe markdown renderer (headings, lists, paragraphs, bold, italics, inline code, front matter skipped). */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("`")) out.push(<code key={key} className="code-inline">{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) out.push(<strong key={key} className="font-semibold text-slate-900 dark:text-white">{tok.slice(2, -2)}</strong>);
    else out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  let text = source.replace(/\r\n/g, "\n");
  if (text.startsWith("---\n")) {
    const end = text.indexOf("\n---", 4);
    if (end > 0) text = text.slice(end + 4);
  }
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { indent: number; text: string }[] = [];
  let ordered = false;

  const flushPara = () => {
    if (para.length) {
      const k = `p${blocks.length}`;
      blocks.push(
        <p key={k} className="text-sm leading-relaxed text-slate-700 dark:text-slate-300">
          {inline(para.join(" "), k)}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      const k = `l${blocks.length}`;
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={k} className={cx("space-y-1 text-sm text-slate-700 dark:text-slate-300", ordered ? "list-decimal pl-5" : "")}>
          {list.map((li, i) => (
            <li key={i} className={cx("flex gap-2", ordered && "list-item")} style={{ marginLeft: li.indent ? li.indent * 8 : 0 }}>
              {!ordered && <span className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-slate-400" />}
              <span className="min-w-0">{inline(li.text, `${k}-${i}`)}</span>
            </li>
          ))}
        </Tag>,
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      const level = h[1].length;
      const k = `h${blocks.length}`;
      blocks.push(
        <p
          key={k}
          className={cx(
            "font-semibold text-slate-900 dark:text-white",
            level === 1 ? "text-base" : level === 2 ? "mt-1 text-sm" : "text-sm",
          )}
        >
          {inline(h[2], k)}
        </p>,
      );
    } else if (li) {
      flushPara();
      const isOrdered = /\d+\./.test(li[2]);
      if (list.length && isOrdered !== ordered) flushList();
      ordered = isOrdered;
      list.push({ indent: Math.floor(li[1].length / 2), text: li[3] });
    } else if (!line.trim()) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return <div className={cx("space-y-2.5", className)}>{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}
