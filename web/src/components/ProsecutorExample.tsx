import { EvidencePanel } from "@/components/EvidencePanel";
import { VerdictStamp } from "@/components/VerdictStamp";
import { prosecutorExample, withheldConcern } from "@/lib/prosecutor-example";

export function ProsecutorExample() {
  const c = prosecutorExample;
  return (
    <section aria-label="Canned Prosecutor example" className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-ink-700 pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
            Canned fixture · exhibit-a/demo-slicer · PR #17
          </p>
          <h2 className="mt-2 font-serif text-2xl text-ink-200">Review comment gate</h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-400">
            The Prosecutor may publish only the concern backed by the execution record below.
          </p>
        </div>
        <VerdictStamp verdict={c.verdict} />
      </header>

      <section aria-label="Evidence and intent claims" className="grid gap-px bg-ink-700 md:grid-cols-2">
        <div className="bg-ink-950 p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-pass">
            Deterministic record
          </p>
          <p className="mt-2 font-serif text-lg text-ink-200">Provable behavior delta</p>
          <p className="mt-1 text-xs text-ink-400">
            Five failures on the PR head; the same test passes on base.
          </p>
          <p className="mt-2 font-mono text-[10px] uppercase tracking-wide text-ink-500">
            Existing suite passed · Suite-gap delta: yes
          </p>
        </div>
        <div className="border border-dashed border-silence/50 bg-ink-900 p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-silence">
            Fallible model read
          </p>
          <p className="mt-2 font-serif text-lg text-ink-200">Judged unintended</p>
          <p className="mt-1 text-xs text-ink-400">{c.intent_rationale}</p>
          <p className="mt-2 font-mono text-[10px] text-ink-500">{c.intent_model}</p>
        </div>
      </section>

      <article className="rounded-md border border-pass/40 bg-ink-900/50">
        <div className="flex items-center justify-between border-b border-ink-700 px-4 py-3">
          <span className="font-serif text-xs uppercase tracking-wide text-pass">
            Comment admitted
          </span>
          <span className="font-mono text-[10px] text-ink-500">5/5 deterministic</span>
        </div>
        <div className="space-y-4 p-4">
          <p className="text-sm text-ink-200">{c.claim_text}</p>
          {c.test_file && (
            <pre className="log-scroll rounded border border-ink-700 bg-ink-950 p-3 font-mono text-xs text-ink-300">
              {c.test_file.code}
            </pre>
          )}
        </div>
      </article>

      <EvidencePanel c={c} />

      <aside className="border-l-2 border-silence/50 bg-ink-900/30 px-4 py-3">
        <p className="font-serif text-xs uppercase tracking-wide text-silence">
          Comment withheld · Silence Log
        </p>
        <p className="mt-2 text-sm text-ink-300">{withheldConcern.hypothesis}</p>
        <p className="mt-1 font-mono text-xs text-ink-500">{withheldConcern.reason}</p>
      </aside>
    </section>
  );
}
