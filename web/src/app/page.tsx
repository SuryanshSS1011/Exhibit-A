"use client";

import { useState } from "react";
import type { Case } from "@/lib/case";
import { VerdictStamp } from "@/components/VerdictStamp";
import { EvidencePanel } from "@/components/EvidencePanel";

/**
 * Detective mode intake + case-file view (plan §4, screen 2).
 * Vertical case timeline: Claim → Hypotheses (rejected greyed) → Evidence → Verdict.
 */
export default function Home() {
  const [repo, setRepo] = useState("../fixtures/buggy_slice");
  const [claim, setClaim] = useState("last_n drops the last row");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Case | null>(null);

  async function investigate() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/investigate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repo, claim }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "engine error");
      setResult(data as Case);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8 border-b border-ink-700 pb-6">
        <h1 className="font-serif text-4xl tracking-stamp text-ink-200">EXHIBIT A</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-400">
          An evidence engine that may only speak with proof — a runnable failing test that
          fails on the broken code and passes on the fix, or honest silence.
        </p>
      </header>

      <section aria-label="Intake" className="mb-8 space-y-3">
        <label className="block">
          <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
            The Claim
          </span>
          <textarea
            value={claim}
            onChange={(e) => setClaim(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-ink-700 bg-ink-900 p-3 font-mono text-sm text-ink-200 outline-none focus:border-ink-400"
            placeholder="Paste a stack trace, error, or a bug description…"
          />
        </label>
        <label className="block">
          <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
            Repo (local path)
          </span>
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            className="w-full rounded-md border border-ink-700 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-ink-400"
          />
        </label>
        <button
          onClick={investigate}
          disabled={busy}
          className="rounded-md border border-ink-400 px-5 py-2 font-serif text-sm uppercase tracking-wide text-ink-200 transition hover:bg-ink-800 disabled:opacity-50"
        >
          {busy ? "Investigating…" : "Investigate"}
        </button>
      </section>

      {error && (
        <div className="mb-6 rounded-md border border-fail/50 bg-fail/10 p-4 text-sm text-fail">
          {error}
        </div>
      )}

      {result && <CaseFile c={result} />}
    </main>
  );
}

function CaseFile({ c }: { c: Case }) {
  return (
    <article aria-label="Case file" className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-serif text-xs uppercase tracking-wide text-ink-400">The Charge</h2>
          <p className="mt-1 max-w-xl text-ink-200">{c.claim_text}</p>
        </div>
        <VerdictStamp verdict={c.verdict} />
      </div>

      <div>
        <h2 className="mb-2 font-serif text-xs uppercase tracking-wide text-ink-400">
          Hypotheses
        </h2>
        <ul className="space-y-1">
          {c.hypotheses.map((h, i) => (
            <li
              key={i}
              className={`text-sm ${h.rejected ? "text-ink-400 line-through" : "text-ink-200"}`}
            >
              {h.text}
              {h.rejected && h.reason && (
                <span className="ml-2 not-italic text-ink-400">— {h.reason}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      {c.verdict === "PROVEN" ? (
        <>
          <EvidencePanel c={c} />
          {c.test_file && (
            <div>
              <h2 className="mb-2 font-serif text-xs uppercase tracking-wide text-ink-400">
                The Test ({c.test_file.path})
              </h2>
              <pre className="log-scroll rounded-md border border-ink-700 bg-ink-950 p-4 font-mono text-xs text-ink-200">
                {c.test_file.code}
              </pre>
            </div>
          )}
        </>
      ) : (
        <div className="rounded-md border border-silence/40 bg-ink-900/40 p-5">
          <h2 className="font-serif text-xs uppercase tracking-wide text-ink-400">
            The Silence Log
          </h2>
          <p className="mt-2 text-sm text-ink-200">
            What I suspected but couldn&apos;t prove:
          </p>
          <p className="mt-1 font-mono text-sm text-silence">{c.silence_reason}</p>
        </div>
      )}
    </article>
  );
}
