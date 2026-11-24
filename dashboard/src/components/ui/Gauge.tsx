import { cx } from "./primitives";

/** Normalises a score that may be a 0..1 fraction or a 0..100 percentage. */
export function toPercent(v: number | null | undefined): number | null {
  if (v == null || Number.isNaN(v)) return null;
  return v <= 1 && v >= 0 ? v * 100 : v;
}

export function scoreColor(pct: number | null): string {
  if (pct == null) return "#94a3b8";
  if (pct >= 90) return "#6366f1";
  if (pct >= 75) return "#34d399";
  if (pct >= 60) return "#fbbf24";
  return "#cc4b4b";
}

export function Gauge({
  value,
  size = 132,
  stroke = 11,
  label,
  sublabel,
  className,
}: {
  value: number | null;
  size?: number;
  stroke?: number;
  label?: string;
  sublabel?: string;
  className?: string;
}) {
  const pct = value == null ? null : Math.max(0, Math.min(100, value));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const arc = 0.75; // 270° gauge
  const dash = c * arc;
  const filled = pct == null ? 0 : (dash * pct) / 100;
  const color = scoreColor(pct);
  return (
    <div className={cx("relative inline-flex flex-col items-center", className)} style={{ width: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-[225deg]">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          className="stroke-slate-200 dark:stroke-slate-800"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${c}`}
          style={{ transition: "stroke-dasharray 0.8s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center" style={{ paddingBottom: size * 0.04 }}>
        <span className="text-3xl font-semibold tabular-nums tracking-tight text-slate-900 dark:text-white" style={{ fontSize: size * 0.24 }}>
          {pct == null ? "—" : pct.toFixed(pct >= 99.95 || pct % 1 === 0 ? 0 : 1)}
        </span>
        {label && <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">{label}</span>}
      </div>
      {sublabel && <span className="-mt-3 text-xs text-slate-500 dark:text-slate-400">{sublabel}</span>}
    </div>
  );
}
