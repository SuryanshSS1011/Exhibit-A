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
      {c.evidence_strength && <StrengthRuler strength={c.evidence_strength} />}
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

function StrengthRuler({ strength }: { strength: NonNullable<Case["evidence_strength"]> }) {
  const components = [
    ["Mutation", strength.mutation],
    ["Signature", strength.signature],
    ["Repeatability", strength.determinism],
    ["Minimality", strength.minimality],
    ["Call distance", strength.surface_distance],
  ] as const;
  const percent = Math.round(strength.composite * 100);
  const coverage = Math.round(strength.coverage * 100);

  return (
    <aside
      aria-label={`Descriptive evidence strength ${percent} out of 100, ${coverage}% measured`}
      className="overflow-hidden border-y border-ink-700 bg-ink-950/50"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h3 className="font-serif text-xs uppercase tracking-[0.16em] text-ink-300">
            Evidence strength
          </h3>
          <span className="font-mono text-2xl tabular-nums text-sky-300">{percent}</span>
          <span className="font-mono text-[10px] uppercase tracking-wide text-ink-500">
            / 100 descriptive
          </span>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-wide text-ink-400">
          {coverage}% measured · never changes verdict
        </p>
      </header>
      <div className="relative h-px bg-ink-700" aria-hidden>
        <div className="absolute inset-y-0 left-0 bg-sky-400" style={{ width: `${percent}%` }} />
      </div>
      <div className="grid grid-cols-2 divide-x divide-y divide-ink-800 sm:grid-cols-5 sm:divide-y-0">
        {components.map(([label, component]) => (
          <div key={label} className="px-3 py-2.5">
            <div className="font-mono text-[9px] uppercase tracking-wide text-ink-500">
              {label}
            </div>
            <div className="mt-1 font-mono text-sm tabular-nums text-ink-200">
              {component.score === null ? "—" : Math.round(component.score * 100)}
            </div>
          </div>
        ))}
      </div>
      <details className="border-t border-ink-800 px-4 py-2 text-xs text-ink-400">
        <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wide hover:text-ink-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-400">
          Show measurement basis
        </summary>
        <dl className="mt-3 grid gap-2 pb-2 sm:grid-cols-2">
          {components.map(([label, component]) => (
            <div key={label}>
              <dt className="font-serif text-[10px] uppercase tracking-wide text-ink-300">
                {label} · weight {Math.round(component.weight * 100)}%
              </dt>
              <dd className="mt-0.5 leading-relaxed">{component.basis}</dd>
            </div>
          ))}
        </dl>
      </details>
    </aside>
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
