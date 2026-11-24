import type { ReactNode } from "react";
import { cx } from "./primitives";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  icon?: ReactNode;
  badge?: ReactNode;
}

export function Tabs<T extends string>({
  items,
  active,
  onChange,
  className,
}: {
  items: TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  className?: string;
}) {
  return (
    <div className={cx("scroll-thin -mb-px flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800", className)} role="tablist">
      {items.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(t.id)}
            className={cx(
              "group inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
              selected
                ? "border-brand-600 text-brand-700 dark:border-brand-400 dark:text-brand-300"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200",
            )}
          >
            {t.icon && <span className={cx(selected ? "" : "text-slate-400 group-hover:text-slate-500")}>{t.icon}</span>}
            {t.label}
            {t.badge}
          </button>
        );
      })}
    </div>
  );
}
