/** @type {import('tailwindcss').Config} */

// Theme: navy chrome, indigo accent, slate neutrals; green / amber / red / violet / cyan for status.

const ink = {
  50: "#f8fafc",
  100: "#f1f5f9",
  200: "#e2e8f0",
  300: "#cbd5e1",
  400: "#94a3b8",
  500: "#64748b",
  600: "#475569",
  700: "#334155",
  800: "#1e293b",
  900: "#0f172a",
  950: "#020617",
};

const accent = {
  50: "#eef2ff",
  100: "#e0e7ff",
  200: "#c7d2fe",
  300: "#a5b4fc",
  400: "#818cf8",
  500: "#6366f1",
  600: "#4f46e5",
  700: "#4338ca",
  800: "#3730a3",
  900: "#312e81",
  950: "#1e1b4b",
};

// slate neutrals
const neutral = {
  50: "#f8fafc",
  100: "#f1f5f9",
  200: "#e2e8f0",
  300: "#cbd5e1",
  400: "#94a3b8",
  500: "#64748b",
  600: "#475569",
  700: "#334155",
  800: "#1e293b",
  900: "#0f172a",
  950: "#020617",
};

const leaf = {
  50: "#ecfdf5",
  100: "#d1fae5",
  200: "#a7f3d0",
  300: "#6ee7b7",
  400: "#34d399",
  500: "#10b981",
  600: "#059669",
  700: "#047857",
  800: "#065f46",
  900: "#064e3b",
  950: "#022c22",
};

const sun = {
  50: "#fffbeb",
  100: "#fef3c7",
  200: "#fde68a",
  300: "#fcd34d",
  400: "#fbbf24",
  500: "#f59e0b",
  600: "#d97706",
  700: "#b45309",
  800: "#92400e",
  900: "#78350f",
  950: "#451a03",
};

const iris = {
  50: "#f5f3ff",
  100: "#ede9fe",
  200: "#ddd6fe",
  300: "#c4b5fd",
  400: "#a78bfa",
  500: "#8b5cf6",
  600: "#7c3aed",
  700: "#6d28d9",
  800: "#5b21b6",
  900: "#4c1d95",
  950: "#2e1065",
};

const aqua = {
  50: "#ecfeff",
  100: "#cffafe",
  200: "#a5f3fc",
  300: "#67e8f9",
  400: "#22d3ee",
  500: "#06b6d4",
  600: "#0891b2",
  700: "#0e7490",
  800: "#155e75",
  900: "#164e63",
  950: "#083344",
};

// restrained red for failures
const red = {
  50: "#fdf3f3",
  100: "#fbe4e4",
  200: "#f5c8c8",
  300: "#ec9f9f",
  400: "#df6f6f",
  500: "#cc4b4b",
  600: "#b53838",
  700: "#962d2d",
  800: "#7b2828",
  900: "#662525",
  950: "#381010",
};

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontSize: {
        sm: ["0.9375rem", { lineHeight: "1.45rem" }],
        base: ["1rem", { lineHeight: "1.6rem" }],
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        ink,
        accent,
        leaf,
        sun,
        iris,
        aqua,
        brand: accent,
        slate: neutral,
        gray: neutral,
        emerald: leaf,
        green: leaf,
        amber: sun,
        yellow: sun,
        orange: sun,
        violet: iris,
        cyan: aqua,
        rose: red,
        red,
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,0.04), 0 1px 3px rgba(15,23,42,0.06)",
        pop: "0 12px 32px -8px rgba(15,23,42,0.20), 0 4px 8px -4px rgba(15,23,42,0.08)",
      },
      keyframes: {
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(99,102,241,0.55)" },
          "70%": { boxShadow: "0 0 0 10px rgba(99,102,241,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(99,102,241,0)" },
        },
        "pulse-amber": {
          "0%": { boxShadow: "0 0 0 0 rgba(245,158,11,0.55)" },
          "70%": { boxShadow: "0 0 0 10px rgba(245,158,11,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(245,158,11,0)" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        blink: {
          "0%, 49%": { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
      },
      animation: {
        "pulse-ring": "pulse-ring 1.6s cubic-bezier(0.4,0,0.6,1) infinite",
        "pulse-amber": "pulse-amber 1.8s cubic-bezier(0.4,0,0.6,1) infinite",
        "fade-in": "fade-in 0.25s ease-out",
        blink: "blink 1s step-end infinite",
      },
    },
  },
  plugins: [],
};
