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
