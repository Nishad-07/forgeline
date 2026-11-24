import { History, Moon, ShieldCheck, Sun, TerminalSquare } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";


import { useTheme } from "../../hooks/useTheme";
import { cx } from "../ui/primitives";

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" fill="#6366f1" />
      <path d="M9.5 22.5 L16 9.5 L22.5 22.5 M12.2 17.6 H19.8" stroke="#0f172a" strokeWidth="2.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}


const NAV = [
  { to: "/", label: "Console", icon: TerminalSquare, end: true },
  { to: "/history", label: "History", icon: History, end: false },
  { to: "/policy", label: "Policy", icon: ShieldCheck, end: false },
];

export function Layout() {
  const { theme, toggle } = useTheme();
  const { pathname } = useLocation();
  return (
    <div className="min-h-screen">
      {import.meta.env.VITE_DEMO_BANNER === "1" && (
        <div className="bg-accent-600 px-4 py-1.5 text-center text-sm text-white">
          Live demo with sample data. Run it locally to point the agents at real models.
        </div>
      )}
      <header className="sticky top-0 z-40 bg-ink-900 text-white shadow-[0_1px_0_rgba(255,255,255,0.06)]">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-2 px-4 sm:gap-4 sm:px-6">
          <NavLink to="/" className="flex shrink-0 items-center gap-2.5" aria-label="Forgeline home">
            <Logo className="h-8 w-8" />
            <div className="leading-tight">
              <p className="text-sm font-bold tracking-tight">Forgeline</p>
              <p className="hidden text-[11px] font-medium text-white/60 md:block">Multi-agent software delivery</p>
            </div>
          </NavLink>
          <nav className="ml-1 flex min-w-0 items-center gap-1 sm:ml-4">
            {NAV.map(({ to, label, icon: Icon, end }) => {
              const active = end ? pathname === to : pathname.startsWith(to) || (to === "/history" && pathname.startsWith("/runs/"));
              return (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  aria-label={label}
                  className={cx(
                    "inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors sm:px-3",
                    active ? "bg-accent-400 text-ink-900" : "text-white/75 hover:bg-white/10 hover:text-white",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden sm:inline">{label}</span>
                </NavLink>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={toggle}
              className="rounded-lg p-2 text-white/75 hover:bg-white/10 hover:text-white"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light theme" : "Dark theme"}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 sm:py-6">
        <Outlet />
      </main>
    </div>
  );
}
