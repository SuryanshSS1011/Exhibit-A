"use client";

import { useEffect, useState } from "react";

interface RateEstimate {
  evaluated: number;
  false_convictions: number;
  rate: number | null;
  lower_95: number | null;
  upper_95: number | null;
}

interface SelfAuditReport {
  id: string;
  created_at: string;
  corpus: string;
  overall: RateEstimate;
  by_category: Record<string, RateEstimate>;
}

export function SelfAudit() {
  const [report, setReport] = useState<SelfAuditReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/self-audit", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.hint ?? payload.error ?? "audit unavailable");
        if (active) setReport(payload.report ?? null);
      })
      .catch((reason) => {
        if (active) setMessage(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section aria-labelledby="audit-title">
      <header className="border-b border-ink-700 pb-5">
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
          Innocent-refactor control group
        </p>
        <h2 id="audit-title" className="mt-2 font-serif text-2xl text-ink-200">
          False-conviction self-audit
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-ink-400">
          Behavior-preserving changes test whether Prosecutor stays silent. Confidence
          intervals remain visible so a small clean corpus cannot masquerade as certainty.
        </p>
      </header>

      {loading && <p className="py-8 font-mono text-xs text-ink-500">Opening audit…</p>}
      {!loading && message && <PrivateNotice message={message} />}
      {!loading && !message && !report && (
        <div className="my-6 border-l-2 border-ink-700 py-3 pl-4 text-sm text-ink-400">
          No audit report yet. Run <code className="font-mono">exhibit-a self-audit</code>.
        </div>
      )}
      {report && <AuditReport report={report} />}
    </section>
  );
}

function AuditReport({ report }: { report: SelfAuditReport }) {
  const estimate = report.overall;
  const rate = estimate.rate ?? 0;
  const lower = estimate.lower_95 ?? 0;
  const upper = estimate.upper_95 ?? 0;
  return (
    <div className="mt-7 space-y-7">
      <div className="grid gap-6 border-y border-ink-700 py-5 md:grid-cols-[11rem_1fr]">
        <div>
          <div className="font-mono text-4xl tabular-nums text-ink-200">
            {formatPercent(estimate.rate)}
          </div>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-wide text-ink-500">
            {estimate.false_convictions}/{estimate.evaluated} false convictions
          </div>
        </div>
        <div className="self-center">
          <div className="flex justify-between font-mono text-[10px] uppercase text-ink-500">
            <span>Wilson 95% interval</span>
            <span>
              {formatPercent(estimate.lower_95)}–{formatPercent(estimate.upper_95)}
            </span>
          </div>
          <div className="relative mt-3 h-5 border-x border-ink-700" aria-hidden>
            <div className="absolute left-0 right-0 top-1/2 h-px bg-ink-700" />
            <div
              className="absolute top-1/2 h-0.5 bg-sky-400"
              style={{ left: `${lower * 100}%`, width: `${(upper - lower) * 100}%` }}
            />
            <div
              className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-ink-200"
              style={{ left: `${rate * 100}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-ink-500">
            An observed zero is not proof of zero risk; expand the control corpus before
            making precision claims.
          </p>
        </div>
      </div>

      <div>
        <h3 className="font-serif text-xs uppercase tracking-wide text-ink-400">
          Controls by refactor type
        </h3>
        <dl className="mt-3 divide-y divide-ink-800 border-y border-ink-800">
          {Object.entries(report.by_category).map(([category, value]) => (
            <div key={category} className="grid grid-cols-[1fr_auto_auto] gap-4 py-3 text-sm">
              <dt className="font-mono text-xs text-ink-300">{labelCategory(category)}</dt>
              <dd className="font-mono text-xs tabular-nums text-ink-400">
                {value.false_convictions}/{value.evaluated}
              </dd>
              <dd className="w-14 text-right font-mono text-xs tabular-nums text-ink-200">
                {formatPercent(value.rate)}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <footer className="font-mono text-[10px] text-ink-600">
        Report {report.id} · {new Date(report.created_at).toLocaleString()}
      </footer>
    </div>
  );
}

function PrivateNotice({ message }: { message: string }) {
  return (
    <div className="my-6 border-l-2 border-silence/50 py-3 pl-4">
      <p className="font-serif text-sm text-ink-300">Private by default.</p>
      <p className="mt-1 text-xs text-ink-500">{message}</p>
    </div>
  );
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function labelCategory(value: string): string {
  return value.replaceAll("_", " ");
}
