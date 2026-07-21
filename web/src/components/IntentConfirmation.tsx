import { useState } from "react";
import type { Case } from "@/lib/case";

export function IntentConfirmation({ c }: { c: Case }) {
  const [choice, setChoice] = useState<"intended" | "regression" | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm(next: "intended" | "regression") {
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/intent-confirmation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          caseId: c.id,
          choice: next,
          rawVerdict: c.verdict,
          disposition: c.disposition,
          intentModel: c.intent_model,
          declaredBehaviorDelta: c.declared_behavior_delta,
        }),
      });
      if (!response.ok) throw new Error("Label could not be recorded");
      setChoice(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }

  if (choice) {
    return (
      <p role="status" className="mt-3 font-mono text-[11px] text-sky-200">
        Private author label recorded: {choice}.
      </p>
    );
  }

  return (
    <div className="mt-3">
      <p className="font-serif text-xs uppercase tracking-wide text-sky-200">
        Author confirmation
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending}
          onClick={() => confirm("intended")}
          className="border border-sky-400/60 px-3 py-1.5 font-mono text-[11px] text-sky-200 transition hover:bg-sky-400/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-300 disabled:opacity-50"
        >
          Intended change
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => confirm("regression")}
          className="border border-red-400/60 px-3 py-1.5 font-mono text-[11px] text-red-300 transition hover:bg-red-400/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-300 disabled:opacity-50"
        >
          Confirm regression
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
    </div>
  );
}
