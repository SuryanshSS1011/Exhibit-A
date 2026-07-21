"use client";

import { useEffect, useState } from "react";
import type { Case } from "@/lib/case";

export function SilenceLog() {
  const [cases, setCases] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [privateGate, setPrivateGate] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const response = await fetch("/api/silence", { cache: "no-store" });
        const payload = await response.json();
        if (response.status === 403) {
          // Private by default — not an error, a deliberate privacy posture.
          if (active) setPrivateGate(payload.hint ?? "Silence Log is private by default.");
          return;
        }
        if (!response.ok) throw new Error(payload.error ?? "could not load silence log");
        if (active) setCases(Array.isArray(payload.cases) ? payload.cases : []);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => {
      active = false;
    };
  }, []);

  return (
    <section aria-labelledby="silence-title">
      <header className="flex flex-col gap-3 border-b border-ink-700 pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
            Negative-results register
          </p>
          <h2 id="silence-title" className="mt-2 font-serif text-2xl text-ink-200">
            What the engine refused to claim
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-400">
            Every row is a suspected defect that failed an admissibility gate. These are
            research results, not review comments.
          </p>
        </div>
        <span className="font-serif text-3xl text-silence">{cases.length}</span>
      </header>

      {loading && !privateGate && (
        <p className="py-8 font-mono text-xs text-ink-500">Opening ledger…</p>
      )}
      {privateGate && (
        <div className="my-6 border-l-2 border-silence/50 py-3 pl-4">
          <p className="font-serif text-sm text-ink-300">Private by default.</p>
          <p className="mt-1 text-xs text-ink-500">
            The Silence Log lists suspected-but-unproven issues and can leak unconfirmed
            findings, so it is never posted publicly. {privateGate}
          </p>
        </div>
      )}
      {error && <p className="py-8 text-sm text-fail">{error}</p>}
      {!loading && !error && cases.length === 0 && (
        <div className="my-6 border-l-2 border-silence/50 py-3 pl-4">
          <p className="font-serif text-sm text-ink-300">No silence recorded yet.</p>
          <p className="mt-1 text-xs text-ink-500">
            Run a claim without a fixed state, or one whose candidate fails the flip check.
          </p>
        </div>
      )}

      <ol className="divide-y divide-ink-800">
        {cases.map((caseFile) => (
          <li key={caseFile.id} className="grid gap-3 py-5 md:grid-cols-[9rem_1fr]">
            <div className="font-mono text-[10px] uppercase text-ink-500">
              <div>{new Date(caseFile.created_at).toLocaleDateString()}</div>
              <div className="mt-1 truncate" title={caseFile.repo ?? undefined}>
                {caseFile.repo ?? "unknown repo"}
              </div>
            </div>
            <div>
              <h3 className="text-sm text-ink-200">{caseFile.claim_text}</h3>
              <p className="mt-2 font-mono text-xs text-silence">
                {caseFile.silence_reason ?? "No admissible flip was produced."}
              </p>
              {caseFile.hypotheses.length > 0 && (
                <ul className="mt-3 space-y-1 border-l border-ink-700 pl-3 text-xs text-ink-500">
                  {caseFile.hypotheses.map((hypothesis, index) => (
                    <li key={index}>{hypothesis.text}</li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
