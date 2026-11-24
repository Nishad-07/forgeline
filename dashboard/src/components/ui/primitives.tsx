import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ Card

export function Card({
  title,
  subtitle,
  icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("card min-w-0", className)}>
      {(title || actions) && (
        <header className="card-header">
          <div className="flex min-w-0 items-center gap-2.5">
            {icon && <span className="text-slate-400 dark:text-slate-500">{icon}</span>}
            <div className="min-w-0">
              {title && <h3 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h3>}
              {subtitle && <p className="truncate text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
            </div>
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName ?? "p-5"}>{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ Badge

export type Tone = "gray" | "blue" | "green" | "amber" | "red" | "violet" | "cyan" | "orange";

const TONE: Record<Tone, string> = {
  gray: "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/20",
  blue: "bg-brand-50 text-brand-700 ring-brand-600/20 dark:bg-brand-500/15 dark:text-brand-300 dark:ring-brand-400/30",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-400/30",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/25 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-400/30",
  red: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/30",
  violet: "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-400/30",
  cyan: "bg-cyan-50 text-cyan-800 ring-cyan-600/20 dark:bg-cyan-500/15 dark:text-cyan-300 dark:ring-cyan-400/30",
  orange: "bg-orange-50 text-orange-700 ring-orange-600/20 dark:bg-orange-500/15 dark:text-orange-300 dark:ring-orange-400/30",
};

const DOT: Record<Tone, string> = {
  gray: "bg-slate-400",
  blue: "bg-brand-500",
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-rose-500",
  violet: "bg-violet-500",
  cyan: "bg-cyan-500",
  orange: "bg-orange-500",
};

export function Badge({
  tone = "gray",
  dot,
  pulse,
  icon,
  children,
  className,
  title,
}: {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONE[tone],
        className,
      )}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5">
          {pulse && <span className={cx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", DOT[tone])} />}
          <span className={cx("relative inline-flex h-1.5 w-1.5 rounded-full", DOT[tone])} />
        </span>
      )}
      {icon}
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ Button

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success" | "warning";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent-400 text-ink-900 font-semibold shadow-sm hover:bg-accent-300 active:bg-accent-500 disabled:bg-accent-400/45 disabled:text-ink-900/60",
  secondary:
    "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 shadow-sm hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700 dark:hover:bg-slate-700",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
  danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-700 disabled:bg-rose-600/50",
  success:
    "bg-accent-400 text-ink-900 font-semibold shadow-sm hover:bg-accent-300 active:bg-accent-500 disabled:bg-accent-400/45 disabled:text-ink-900/60",
  warning: "bg-sun-400 text-sun-950 font-semibold shadow-sm hover:bg-sun-300 disabled:bg-sun-400/50",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" && "px-2.5 py-1.5 text-xs",
        size === "md" && "px-3.5 py-2 text-sm",
        size === "lg" && "px-5 py-2.5 text-sm",
        VARIANT[variant],
        className,
      )}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ states

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("h-4 w-4 animate-spin text-slate-400", className)} />;
}

export function LoadingBlock({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div className={cx("flex items-center justify-center gap-2 py-12 text-sm text-slate-500 dark:text-slate-400", className)}>
      <Spinner />
      {label}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-slate-200/70 dark:bg-slate-800", className)} />;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500">
        {icon ?? <Inbox className="h-5 w-5" />}
      </div>
      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{title}</p>
      {description && <p className="mt-1 max-w-md text-sm text-slate-500 dark:text-slate-400">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: Error | string; onRetry?: () => void; className?: string }) {
  const message = typeof error === "string" ? error : error.message;
  return (
    <div
      className={cx(
        "flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200",
        className,
      )}
      role="alert"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">Something went wrong</p>
        <p className="mt-0.5 break-words text-rose-700/90 dark:text-rose-200/80">{message}</p>
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function InlineAlert({
  tone = "red",
  title,
  children,
  icon,
  className,
}: {
  tone?: "red" | "amber" | "blue" | "green";
  title?: ReactNode;
  children?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  const styles = {
    red: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200",
    amber: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
    blue: "border-brand-200 bg-brand-50 text-brand-900 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200",
    green: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200",
  }[tone];
  return (
    <div className={cx("flex gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm", styles, className)} role={tone === "red" ? "alert" : undefined}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cx(title ? "mt-0.5" : "", "break-words opacity-90")}>{children}</div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ bars & stats

export function ProgressBar({
  value,
  max = 100,
  tone = "blue",
  className,
  size = "md",
}: {
  value: number;
  max?: number;
  tone?: "blue" | "green" | "amber" | "red" | "violet" | "auto";
  className?: string;
  size?: "sm" | "md";
}) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const resolved = tone === "auto" ? (pct >= 90 ? "green" : pct >= 70 ? "amber" : "red") : tone;
  const color = {
    blue: "bg-brand-500",
    green: "bg-emerald-500",
    amber: "bg-amber-500",
    red: "bg-rose-500",
    violet: "bg-violet-500",
  }[resolved];
  return (
    <div className={cx("w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800", size === "sm" ? "h-1.5" : "h-2", className)}>
      <div className={cx("h-full rounded-full transition-all duration-500", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  icon,
  tone = "blue",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="card flex items-start gap-3.5 p-4">
      {icon && (
        <div className={cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset", TONE[tone])}>{icon}</div>
      )}
      <div className="min-w-0">
        <p className="label">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-slate-900 dark:text-white">{value}</p>
        {sub && <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
      </div>
    </div>
  );
}

export function SectionTitle({ children, right, className }: { children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cx("mb-2.5 flex items-center justify-between gap-2", className)}>
      <h4 className="label">{children}</h4>
      {right}
    </div>
  );
}

export function KeyValue({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="divide-y divide-slate-100 dark:divide-slate-800">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-start justify-between gap-4 py-2 text-sm">
          <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="text-right font-medium text-slate-900 dark:text-slate-100">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function BulletList({ items, empty = "None", tone = "slate" }: { items?: string[]; empty?: string; tone?: "slate" | "amber" | "red" }) {
  if (!items || !items.length) return <p className="text-sm text-slate-400 dark:text-slate-500">{empty}</p>;
  const dot = { slate: "bg-slate-400", amber: "bg-amber-500", red: "bg-rose-500" }[tone];
  return (
    <ul className="space-y-1.5">
      {items.map((it, i) => (
        <li key={i} className="flex gap-2.5 text-sm text-slate-700 dark:text-slate-300">
          <span className={cx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
          <span className="min-w-0 break-words">{it}</span>
        </li>
      ))}
    </ul>
  );
}
