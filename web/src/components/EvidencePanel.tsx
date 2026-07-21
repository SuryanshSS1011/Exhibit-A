import type { Case } from "@/lib/case";

/**
 * The two-tab evidence panel rendered side-by-side (plan §4 legibility principle:
 * "always show both states adjacently — proof is the contrast"). We show the raw
 * log, never a paraphrase: the model's summary is not evidence; the terminal is.
 */
export function EvidencePanel({ c }: { c: Case }) {
  const cmd = c.run_command;
  return (
    <section aria-label="Evidence" className="space-y-4">
      <div
        className={`grid grid-cols-1 gap-4 ${c.evidence.control_log ? "lg:grid-cols-3" : "md:grid-cols-2"}`}
      >
        <EvidenceColumn
          title="Fails on the buggy code"
          accent="fail"
          command={cmd}
          log={c.evidence.fail_log}
          signature={c.evidence.fail_signature}
        />
        <EvidenceColumn
          title="Passes on the base"
          accent="pass"
          command={cmd}
          log={c.evidence.pass_log}
          signature={null}
          empty={c.evidence.pass_log ? undefined : "No base/fixed state was proven."}
        />
        {c.evidence.control_log && (
          <EvidenceColumn
            title="Survives unrelated control"
            accent="control"
            command={cmd}
            log={c.evidence.control_log}
            signature={null}
          />
        )}
      </div>
      {c.culprit_commit && c.culprit_parent_commit && (
        <div className="border-l-2 border-sky-400 bg-ink-900/50 px-4 py-3">
          <p className="font-serif text-xs uppercase tracking-wide text-sky-300">
            Causal boundary verified
          </p>
          <p className="mt-2 font-mono text-xs text-ink-300">
            {c.culprit_parent_commit} → {c.culprit_commit}
          </p>
          {c.evidence.bisect_log && (
            <details className="mt-2 text-xs text-ink-400">
              <summary className="cursor-pointer">Show git bisect log</summary>
              <pre className="log-scroll mt-2 max-h-48 overflow-auto bg-ink-950 p-3 font-mono text-ink-300">
                {c.evidence.bisect_log}
              </pre>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function EvidenceColumn({
  title,
  accent,
  command,
  log,
  signature,
  empty,
}: {
  title: string;
  accent: "fail" | "pass" | "control";
  command: string;
  log: string;
  signature: string | null;
  empty?: string;
}) {
  const border =
    accent === "fail"
      ? "border-fail/50"
      : accent === "pass"
        ? "border-pass/50"
        : "border-amber-500/50";
  const dot = accent === "fail" ? "bg-fail" : accent === "pass" ? "bg-pass" : "bg-amber-500";
  return (
    <div className={`rounded-md border ${border} bg-ink-900/60`}>
      <header className="flex items-center gap-2 border-b border-ink-700 px-4 py-2">
        <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden />
        <h3 className="font-serif text-sm uppercase tracking-wide text-ink-200">{title}</h3>
      </header>
      <div className="space-y-2 p-4">
        <code className="block text-xs text-ink-400">$ {command}</code>
        {signature && (
          <div className="font-mono text-xs text-fail">{signature}</div>
        )}
        {empty ? (
          <p className="text-xs italic text-ink-400">{empty}</p>
        ) : (
          <pre className="log-scroll max-h-64 overflow-y-auto rounded bg-ink-950 p-3 font-mono text-xs leading-relaxed text-ink-200">
            {log || "(no output captured)"}
          </pre>
        )}
      </div>
    </div>
  );
}
