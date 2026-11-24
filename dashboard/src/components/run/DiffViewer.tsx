import { ChevronDown, ChevronRight, FileCode2, FilePlus2 } from "lucide-react";
import { useMemo, useState } from "react";
import { cx } from "../ui/primitives";

type LineKind = "add" | "del" | "ctx" | "hunk" | "meta";
interface DiffLine {
  kind: LineKind;
  text: string;
  oldNo?: number;
  newNo?: number;
}
interface DiffFile {
  path: string;
  oldPath: string;
  isNew: boolean;
  isDeleted: boolean;
  added: number;
  removed: number;
  lines: DiffLine[];
}

/** Parses a unified diff (git format or plain ---/+++) into files, hunks and numbered lines. */
export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;
  const start = (path = "", oldPath = "") => {
    cur = { path, oldPath, isNew: false, isDeleted: false, added: 0, removed: 0, lines: [] };
    files.push(cur);
    return cur;
  };
  const all = text.replace(/\r\n/g, "\n").split("\n");
  for (let idx = 0; idx < all.length; idx++) {
    const raw = all[idx];
    const isHeaderPair = raw.startsWith("--- ") && (all[idx + 1] ?? "").startsWith("+++ ");
    if (raw.startsWith("diff --git ")) {
      const m = /^diff --git a\/(.+?) b\/(.+)$/.exec(raw);
      start(m?.[2] ?? raw.slice(11), m?.[1] ?? "");
      continue;
    }
    if (isHeaderPair && (!cur || (cur as DiffFile).lines.length > 0)) {
      // plain diff without "diff --git" headers
      start();
    }
    const f: DiffFile = cur ?? start();
    if (isHeaderPair) {
      const p = raw.slice(4).replace(/^a\//, "");
      if (p === "/dev/null") f.isNew = true;
      else f.oldPath = f.oldPath || p;
      continue;
    }
    if (raw.startsWith("+++ ") && (all[idx - 1] ?? "").startsWith("--- ")) {
      const p = raw.slice(4).replace(/^b\//, "");
      if (p === "/dev/null") f.isDeleted = true;
      else f.path = f.path || p;
      continue;
    }
    if (raw.startsWith("new file mode")) {
      f.isNew = true;
      continue;
    }
    if (raw.startsWith("deleted file mode")) {
      f.isDeleted = true;
      continue;
    }
    if (raw.startsWith("index ") || raw.startsWith("similarity ") || raw.startsWith("rename ") || raw.startsWith("old mode") || raw.startsWith("new mode")) continue;
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(raw);
    if (h) {
      oldNo = Number(h[1]);
      newNo = Number(h[2]);
      f.lines.push({ kind: "hunk", text: raw });
      continue;
    }
    if (!f.lines.some((l) => l.kind === "hunk")) continue; // header noise
    if (raw.startsWith("+")) {
      f.lines.push({ kind: "add", text: raw.slice(1), newNo: newNo++ });
      f.added++;
    } else if (raw.startsWith("-")) {
      f.lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++ });
      f.removed++;
    } else if (raw.startsWith("\\")) {
      f.lines.push({ kind: "meta", text: raw });
    } else if (raw === "") {
      // trailing blank line at end of diff text; treat as context only inside hunks
      f.lines.push({ kind: "ctx", text: "", oldNo: oldNo++, newNo: newNo++ });
    } else {
      f.lines.push({ kind: "ctx", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  // drop trailing empty context lines produced by the final newline
  for (const f of files) {
    while (f.lines.length && f.lines[f.lines.length - 1].kind === "ctx" && f.lines[f.lines.length - 1].text === "") f.lines.pop();
  }
  return files.filter((f) => f.path || f.oldPath);
}

function FileBlock({ file }: { file: DiffFile }) {
  const [open, setOpen] = useState(true);
  const total = file.added + file.removed || 1;
  const blocks = 5;
  const addBlocks = Math.round((file.added / total) * blocks);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 bg-slate-50 px-3 py-2 text-left hover:bg-slate-100 dark:bg-slate-900 dark:hover:bg-slate-800/80"
      >
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        {file.isNew ? <FilePlus2 className="h-4 w-4 text-emerald-500" /> : <FileCode2 className="h-4 w-4 text-slate-400" />}
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium text-slate-800 dark:text-slate-200">{file.path || file.oldPath}</span>
        {file.isNew && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">new</span>}
        <span className="font-mono text-xs font-semibold text-emerald-600 dark:text-emerald-400">+{file.added}</span>
        <span className="font-mono text-xs font-semibold text-rose-600 dark:text-rose-400">−{file.removed}</span>
        <span className="ml-1 hidden gap-0.5 sm:flex" aria-hidden>
          {Array.from({ length: blocks }).map((_, i) => (
            <span key={i} className={cx("h-2 w-2 rounded-sm", i < addBlocks ? "bg-emerald-500" : i < blocks && file.removed ? "bg-rose-500" : "bg-slate-300 dark:bg-slate-700")} />
          ))}
        </span>
      </button>
      {open && (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[12px] leading-[1.55]">
            <tbody>
              {file.lines.map((l, i) => {
                if (l.kind === "hunk")
                  return (
                    <tr key={i} className="bg-brand-50/70 text-brand-700 dark:bg-brand-500/10 dark:text-brand-300">
                      <td colSpan={3} className="select-none px-3 py-1 text-[11px]">
                        {l.text}
                      </td>
                    </tr>
                  );
                const row =
                  l.kind === "add"
                    ? "bg-emerald-50 dark:bg-emerald-500/10"
                    : l.kind === "del"
                      ? "bg-rose-50 dark:bg-rose-500/10"
                      : "";
                const num =
                  l.kind === "add"
                    ? "bg-emerald-100/70 text-emerald-700/70 dark:bg-emerald-500/15 dark:text-emerald-400/70"
                    : l.kind === "del"
                      ? "bg-rose-100/70 text-rose-700/70 dark:bg-rose-500/15 dark:text-rose-400/70"
                      : "text-slate-400 dark:text-slate-600";
                return (
                  <tr key={i} className={row}>
                    <td className={cx("w-10 select-none border-r border-slate-200/70 px-2 text-right tabular-nums dark:border-slate-800", num)}>{l.oldNo ?? ""}</td>
                    <td className={cx("w-10 select-none border-r border-slate-200/70 px-2 text-right tabular-nums dark:border-slate-800", num)}>{l.newNo ?? ""}</td>
                    <td className="whitespace-pre px-3 text-slate-800 dark:text-slate-200">
                      <span
                        className={cx(
                          "mr-2 inline-block w-2 select-none",
                          l.kind === "add" ? "text-emerald-600 dark:text-emerald-400" : l.kind === "del" ? "text-rose-600 dark:text-rose-400" : "text-transparent",
                        )}
                      >
                        {l.kind === "add" ? "+" : l.kind === "del" ? "-" : " "}
                      </span>
                      {l.text}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function DiffViewer({ diff }: { diff: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  if (!files.length) return <pre className="scroll-thin overflow-x-auto rounded-xl bg-slate-50 p-4 font-mono text-xs dark:bg-slate-900">{diff}</pre>;
  return (
    <div className="space-y-3">
      {files.map((f, i) => (
        <FileBlock key={`${f.path}-${i}`} file={f} />
      ))}
    </div>
  );
}
