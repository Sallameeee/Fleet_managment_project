import type { Config } from "tailwindcss";

// Theming strategy: the `ink` (surfaces), `slate` (text), `white` (strong text),
// and `brand.sage` colors are driven by CSS variables that flip between dark
// (default, :root) and light (html.light) in globals.css. This themes every
// existing class (bg-ink-*, text-slate-*, text-white, text-brand-sage) without
// editing component markup. brand DEFAULT/primary stay fixed so buttons keep
// the brand green in both modes.
const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        white: "rgb(var(--c-strong) / <alpha-value>)",
        brand: {
          DEFAULT: "#3AA76D", // primary green (fixed)
          primary: "#3AA76D",
          sage: "rgb(var(--c-brand-sage) / <alpha-value>)", // readable accent in both modes
          dark: "#2c8255",
        },
        ink: {
          950: "rgb(var(--c-ink-950) / <alpha-value>)",
          900: "rgb(var(--c-ink-900) / <alpha-value>)",
          850: "rgb(var(--c-ink-850) / <alpha-value>)",
          800: "rgb(var(--c-ink-800) / <alpha-value>)",
          700: "rgb(var(--c-ink-700) / <alpha-value>)",
        },
        slate: {
          100: "rgb(var(--c-slate-100) / <alpha-value>)",
          200: "rgb(var(--c-slate-200) / <alpha-value>)",
          300: "rgb(var(--c-slate-300) / <alpha-value>)",
          400: "rgb(var(--c-slate-400) / <alpha-value>)",
          500: "rgb(var(--c-slate-500) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};

export default config;
