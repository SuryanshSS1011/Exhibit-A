import type { Verdict } from "@/lib/case";

/**
 * The hero element (plan §4): a large, unmissable stamp. The binary IS the product.
 * PROVEN reads as an admitted exhibit; INSUFFICIENT EVIDENCE reads as honest silence,
 * not an error.
 */
export function VerdictStamp({ verdict }: { verdict: Verdict }) {
  const proven = verdict === "PROVEN";
  return (
    <div
      role="status"
      aria-label={`Verdict: ${verdict}`}
      className={[
        "inline-flex select-none items-center rounded-sm border-4 px-6 py-3",
        "font-serif text-2xl uppercase tracking-stamp",
        "rotate-[-3deg]",
        proven
          ? "border-proven text-proven"
          : "border-silence text-silence opacity-80",
      ].join(" ")}
    >
      {proven ? "Proven" : "Insufficient Evidence"}
    </div>
  );
}
