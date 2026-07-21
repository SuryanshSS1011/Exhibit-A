import type { Config } from "tailwindcss";

/**
 * Design tokens for the "case file" metaphor (plan §4).
 * Colour discipline is deliberate: `proven`/`fail`/`pass` MEAN something and are
 * the only saturated colours. Everything else is ink-on-parchment neutrals.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Parchment / manila — the dossier surface (used sparingly, light mode).
        parchment: {
          50: "#faf7f0",
          100: "#f4ecd9",
          200: "#e7d8b5",
        },
        // Ink — the serious neutral scale (dark mode default).
        ink: {
          950: "#0c0c0e",
          900: "#141417",
          800: "#1d1d21",
          700: "#2a2a30",
          400: "#8b8b93",
          200: "#cbcbd2",
        },
        // Verdict semantics — the ONLY decorative-looking colours, and they aren't decorative.
        proven: "#2f9e5f", // green stamp: PROVEN
        fail: "#d1443f", // red: the failing assertion line, on the buggy state
        pass: "#2f9e5f", // green: the passing run, on the base state
        silence: "#6b6b73", // grey stamp: INSUFFICIENT EVIDENCE
      },
      fontFamily: {
        // Restrained serif for the section headers ("THE CHARGE", "THE VERDICT").
        serif: ["Georgia", "Cambria", "Times New Roman", "serif"],
        // Monospace for all code and raw logs (evidence is never paraphrased).
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
      },
      letterSpacing: {
        stamp: "0.18em",
      },
    },
  },
  plugins: [],
};

export default config;
