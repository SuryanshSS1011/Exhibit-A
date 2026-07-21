"use client";

import { useState } from "react";
import type { Case } from "@/lib/case";
import { isEvidence } from "@/lib/case";
import { VerdictStamp } from "@/components/VerdictStamp";
import { EvidencePanel } from "@/components/EvidencePanel";
import { ProsecutorExample } from "@/components/ProsecutorExample";
import { SilenceLog } from "@/components/SilenceLog";

interface InvestigationEvent {
  event: string;
  [key: string]: unknown;
}

/**
 * Detective mode intake + case-file view (plan §4, screen 2).
 * Vertical case timeline: Claim → Hypotheses (rejected greyed) → Evidence → Verdict.
 */
export default function Home() {
  const [workspace, setWorkspace] = useState<"detective" | "prosecutor" | "silence">(
    "detective",
  );
  const [sourceMode, setSourceMode] = useState<"local" | "git">("local");
  const [repo, setRepo] = useState("../fixtures/buggy_inventory");
  const [fixed, setFixed] = useState("../fixtures/fixed_inventory");
  const [control, setControl] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [baseSha, setBaseSha] = useState("");
  const [fixSha, setFixSha] = useState("");
  const [controlSha, setControlSha] = useState("");
  const [claim, setClaim] = useState(
    "stock_for should return zero for an unknown SKU instead of raising KeyError",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Case | null>(null);
  const [events, setEvents] = useState<InvestigationEvent[]>([]);

  async function investigate(replay?: "proven" | "silence") {
    setBusy(true);
    setError(null);
    setResult(null);
    setEvents([]);
    try {
      const res = await fetch("/api/investigate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          replay
            ? { replay }
            : sourceMode === "local"
              ? { repo, fixed, control, claim }
              : { repoUrl, baseSha, fixSha, controlSha, claim },
        ),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error ?? "engine error");
      }
      if (!res.body) throw new Error("engine returned no event stream");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = parseEventBlock(block);
          if (!event) continue;
          if (event.event === "error") {
            throw new Error(asString(event.error) ?? "engine stream failed");
          }
          if (event.event === "case" && isCase(event.case)) {
            setResult(event.case);
          } else {
            setEvents((current) => [...current, event]);
          }
        }
        if (done) break;
      }
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

      <nav aria-label="Investigation mode" className="mb-8 flex gap-6 border-b border-ink-700">
        {(["detective", "prosecutor", "silence"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setWorkspace(mode)}
            aria-current={workspace === mode ? "page" : undefined}
            className={`border-b-2 px-1 pb-3 font-serif text-xs uppercase tracking-[0.16em] transition ${
              workspace === mode
                ? "border-ink-200 text-ink-200"
                : "border-transparent text-ink-500 hover:text-ink-300"
            }`}
          >
            {mode === "detective"
              ? "Detective"
              : mode === "prosecutor"
                ? "Prosecutor fixture"
                : "Silence ledger"}
          </button>
        ))}
      </nav>

      {workspace === "detective" ? (
        <>
      <section
        aria-label="Sealed demo exhibits"
        className="mb-8 grid overflow-hidden rounded-md border border-ink-700 bg-ink-950 md:grid-cols-[1fr_auto]"
      >
        <div className="border-b border-ink-700 px-4 py-3 md:border-b-0 md:border-r">
          <h2 className="font-serif text-xs uppercase tracking-[0.16em] text-ink-300">
            Sealed demo exhibits
          </h2>
          <p className="mt-1 text-xs text-ink-400">
            Open a recorded Case when stage conditions make a live model run impractical.
          </p>
        </div>
        <div className="grid grid-cols-2 divide-x divide-ink-700">
          <button
            type="button"
            onClick={() => investigate("proven")}
            disabled={busy}
            className="group min-w-32 px-4 py-3 text-left transition hover:bg-pass/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-pass disabled:opacity-50"
          >
            <span className="block font-mono text-[10px] uppercase tracking-wide text-pass">
              Exhibit P
            </span>
            <span className="mt-1 block font-serif text-xs uppercase text-ink-200">
              Replay proof
            </span>
          </button>
          <button
            type="button"
            onClick={() => investigate("silence")}
            disabled={busy}
            className="group min-w-32 px-4 py-3 text-left transition hover:bg-silence/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-silence disabled:opacity-50"
          >
            <span className="block font-mono text-[10px] uppercase tracking-wide text-silence">
              Exhibit S
            </span>
            <span className="mt-1 block font-serif text-xs uppercase text-ink-200">
              Replay silence
            </span>
          </button>
        </div>
      </section>
      <section aria-label="Intake" className="mb-8 space-y-3">
        <div className="flex border-b border-ink-700" role="group" aria-label="Source type">
          {(["local", "git"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={sourceMode === mode}
              onClick={() => setSourceMode(mode)}
              className={`border-x border-t px-4 py-2 font-serif text-xs uppercase tracking-wide transition ${
                sourceMode === mode
                  ? "border-ink-400 bg-ink-800 text-ink-200"
                  : "border-transparent text-ink-400 hover:text-ink-200"
              }`}
            >
              {mode === "local" ? "Local checkouts" : "Git commits"}
            </button>
          ))}
        </div>
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
        {sourceMode === "local" ? (
          <>
            <label className="block">
              <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                Reported State (local path)
              </span>
              <input
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                className="w-full rounded-md border border-ink-700 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-ink-400"
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                Known Fixed State
              </span>
              <input
                value={fixed}
                onChange={(e) => setFixed(e.target.value)}
                className="w-full rounded-md border border-ink-700 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-ink-400"
                placeholder="Optional path to a fixed checkout"
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                Unrelated Control State · Optional
              </span>
              <input
                value={control}
                onChange={(e) => setControl(e.target.value)}
                className="w-full rounded-md border border-amber-500/30 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-amber-500"
                placeholder="Older checkout where the candidate must pass"
              />
            </label>
          </>
        ) : (
          <div className="space-y-3 border-l-2 border-ink-700 pl-4">
            <label className="block">
              <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                HTTPS Repository URL
              </span>
              <input
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                className="w-full rounded-md border border-ink-700 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-ink-400"
                placeholder="https://github.com/org/repo.git"
              />
            </label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                  Base SHA · Buggy
                </span>
                <input
                  value={baseSha}
                  onChange={(e) => setBaseSha(e.target.value)}
                  className="w-full rounded-md border border-fail/40 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-fail"
                  placeholder="7–40 hex characters"
                />
              </label>
              <label className="block">
                <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                  Fix SHA · Passing
                </span>
                <input
                  value={fixSha}
                  onChange={(e) => setFixSha(e.target.value)}
                  className="w-full rounded-md border border-pass/40 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-pass"
                  placeholder="7–40 hex characters"
                />
              </label>
            </div>
            <label className="block">
              <span className="mb-1 block font-serif text-xs uppercase tracking-wide text-ink-400">
                Control SHA · Older / Unrelated · Optional
              </span>
              <input
                value={controlSha}
                onChange={(e) => setControlSha(e.target.value)}
                className="w-full rounded-md border border-amber-500/30 bg-ink-900 p-2 font-mono text-sm text-ink-200 outline-none focus:border-amber-500"
                placeholder="7–40 hex characters"
              />
            </label>
          </div>
        )}
        <p className="text-xs text-ink-400">
          A PROVEN verdict requires both sides: fail on the reported state, pass on the fix.
        </p>
        <button
          onClick={() => investigate()}
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

      {(busy || events.length > 0) && <LiveDocket events={events} busy={busy} />}

      {result && <CaseFile c={result} />}
        </>
      ) : workspace === "prosecutor" ? (
        <ProsecutorExample />
      ) : (
        <SilenceLog />
      )}
    </main>
  );
}

function LiveDocket({ events, busy }: { events: InvestigationEvent[]; busy: boolean }) {
  const runs = events.filter((event) => event.event === "run");
  const latestRun = runs.at(-1);
  return (
    <section
      aria-label="Live investigation log"
      className="mb-8 overflow-hidden rounded-md border border-ink-700 bg-ink-950"
    >
      <header className="flex items-center justify-between border-b border-ink-700 px-4 py-2">
        <h2 className="font-serif text-xs uppercase tracking-wide text-ink-300">
          Chain of Custody
        </h2>
        <span className="flex items-center gap-2 font-mono text-[10px] uppercase text-ink-400">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              busy ? "bg-fail motion-safe:animate-pulse" : "bg-pass"
            }`}
          />
          {busy ? "Recording" : "Sealed"}
        </span>
      </header>
      <ol className="max-h-48 space-y-1 overflow-y-auto px-4 py-3 font-mono text-xs">
        {events.length === 0 && <li className="text-ink-400">Opening case…</li>}
        {events.map((event, index) => (
          <li key={index} className="flex gap-3 text-ink-300">
            <span className="w-6 shrink-0 text-ink-600">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span>{eventLabel(event)}</span>
          </li>
        ))}
      </ol>
      {latestRun && asString(latestRun.log) && (
        <pre className="log-scroll max-h-48 overflow-auto border-t border-ink-800 bg-black/30 p-4 font-mono text-[11px] leading-relaxed text-ink-400">
          {asString(latestRun.log)}
        </pre>
      )}
    </section>
  );
}

function parseEventBlock(block: string): InvestigationEvent | null {
  const data = block
    .split("\n")
    .find((line) => line.startsWith("data: "))
    ?.slice(6);
  if (!data) return null;
  const parsed: unknown = JSON.parse(data);
  if (!parsed || typeof parsed !== "object" || !("event" in parsed)) return null;
  return parsed as InvestigationEvent;
}

function eventLabel(event: InvestigationEvent): string {
  if (event.event === "phase") return asString(event.message) ?? "Advancing investigation";
  if (event.event === "hypothesis") return `Hypothesis admitted: ${asString(event.text)}`;
  if (event.event === "run") {
    const state =
      event.state === "target" ? "buggy" : event.state === "control" ? "control" : "fixed";
    const outcome = event.passed ? "PASS" : "FAIL";
    return `${state} run ${event.attempt}/${event.total} · ${outcome}`;
  }
  if (event.event === "rejected") return `Rejected · ${asString(event.reason)}`;
  if (event.event === "verdict") return `Verdict · ${asString(event.verdict)}`;
  return event.event;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function isCase(value: unknown): value is Case {
  return Boolean(
    value &&
      typeof value === "object" &&
      "verdict" in value &&
      "evidence" in value &&
      "hypotheses" in value,
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
        <VerdictStamp disposition={c.disposition} />
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

      {isEvidence(c) ? (
        <>
          {c.disposition === "BEHAVIOR_CHANGE" && (
            <div className="rounded-md border border-sky-400/40 bg-sky-400/10 p-3 text-xs text-sky-200">
              Confirm this is intended. Execution proves a behavior delta; intent remains a
              separate, fallible interpretation.
              {c.declared_behavior_delta && (
                <span className="mt-2 block font-mono text-[11px] text-sky-300">
                  Declared delta: {c.declared_behavior_delta}
                </span>
              )}
            </div>
          )}
          {c.verdict === "REPRODUCED" && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-500">
              Reproduced, not fully proven: a deterministic, signature-matched failure with
              no fixed state to flip against. Evidence of a bug&apos;s presence — not a proven
              regression against a known-good baseline.
            </div>
          )}
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
