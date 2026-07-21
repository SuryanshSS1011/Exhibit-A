import type { Verdict } from "@/lib/case";

/**
 * The hero element (plan §4): a large, unmissable stamp. Three tiers, honestly
 * distinguished so the tool never overclaims:
 *   PROVEN       — a full flip (fails on target, passes on base). Strongest.
 *   REPRODUCED   — signature-matched failure with no proven pass state (the common
 *                  live-bug case). Real evidence, but weaker than a flip.
 *   INSUFFICIENT — honest silence, not an error.
 */
const STYLES: Record<Verdict, { label: string; cls: string }> = {
  PROVEN: { label: "Proven", cls: "border-proven text-proven" },
  REPRODUCED: { label: "Reproduced", cls: "border-amber-500 text-amber-500" },
  INSUFFICIENT_EVIDENCE: {
    label: "Insufficient Evidence",
    cls: "border-silence text-silence opacity-80",
  },
};

export function VerdictStamp({ verdict }: { verdict: Verdict }) {
  const { label, cls } = STYLES[verdict];
  return (
    <div
      role="status"
      aria-label={`Verdict: ${verdict}`}
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
