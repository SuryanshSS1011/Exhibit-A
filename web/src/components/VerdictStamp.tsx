import type { Disposition } from "@/lib/case";

/**
 * The hero element (plan §4): a large, unmissable stamp. Four states, honestly
 * distinguished so the tool never overclaims:
 *   VERIFIED       — a full flip (fails on target, passes on base). Strongest.
 *   PARTIAL        — signature-matched failure with no proven pass state (the common
 *                  live-bug case). Real evidence, but weaker than a flip.
 *   FAILED         — reserved for evidence that disproves the stated goal.
 *   UNCERTAIN      — honest silence, not an error.
 */
const STYLES: Record<Disposition, { label: string; cls: string }> = {
  PROVEN_REGRESSION: { label: "Proven Regression", cls: "border-red-500 text-red-400" },
  BEHAVIOR_CHANGE: { label: "Behavior Change", cls: "border-sky-400 text-sky-300" },
  PARTIAL: { label: "Reproduced", cls: "border-amber-500 text-amber-500" },
  FAILED: { label: "Failed", cls: "border-red-400 text-red-300" },
  UNCERTAIN: {
    label: "Insufficient Evidence",
    cls: "border-silence text-silence opacity-80",
  },
};

export function VerdictStamp({ disposition }: { disposition: Disposition }) {
  const { label, cls } = STYLES[disposition];
  return (
    <div
      role="status"
      aria-label={`Disposition: ${disposition}`}
      className={[
        "inline-flex select-none items-center rounded-sm border-4 px-6 py-3",
        "font-serif text-2xl uppercase tracking-stamp",
        "rotate-[-3deg]",
        cls,
      ].join(" ")}
    >
      {label}
    </div>
  );
}
