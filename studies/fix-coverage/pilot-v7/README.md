# Fix-coverage pilot v7

Pilot v7 measures whether the engine-side reach improvements derived from v6 generalize to
a fresh corpus. Its corpus must be disjoint from both v5 and v6. The 60 prior PR URLs are
frozen in `prior-corpora.json` and mechanically excluded before the repository cap.

The method requires a provider-free `--probe` over the final corpus before the provider
pilot. If fewer than 3/30 instances reach the deterministic judge, v7 stops without a
model call and publishes that plumbing result. Otherwise the provider run uses the same
corpus with minimization disabled. Environment-install failures are measured and
classified but not fixed during this pilot.

This preregistration and prior-corpus union are committed before v7 selection, probe,
provider calls, container builds, test executions, Cases, or verdicts.

The mechanical selector subsequently froze 30 disjoint fixes across 21 repositories. The
hash-pinned [`corpus.json`](./corpus.json) and
[`selection-report.md`](./selection-report.md) preserve all 60 prior-corpus exclusions, 42
ordinary eligibility exclusions, and 937 eligible candidates outside the fixed sample.
Twenty-seven selected instances use `uv.lock`, two use Poetry, and one uses pinned
requirements. No probe outcome existed when the corpus was committed.

Before instance one, the runner rejected the generated `plotly.py` instance ID because
it contained a period. [`amendment-001.json`](./amendment-001.json) records the
identifier-only correction made before any clone, execution, outcome, or provider call.
The selected PR and every substantive corpus field remain unchanged; the selector itself
was not changed during v7.

The completed provider-free probe reached the judge on **15/30 instances (50.0%)** and
failed dependency installation on the other 15. It made zero provider calls and therefore
does not measure verification. The result exceeds the preregistered 3/30 gate, authorizing
the provider pilot on the unchanged corpus. See the human-readable
[`probe-result.md`](./probe-result.md) and machine-readable
[`probe-report.json`](./probe-report.json).

The completed provider run also reached the judge on **15/30 (50.0%)**. It produced
**7/30 VERIFIED (23.3%)**, **0/30 PARTIAL**, and eight classified candidate rejections.
Dependency installation blocked 15/30 on the recorded Docker `linux/arm64` platform.
Minimization remained disabled. An explicit quota response halted after 16 completed
instances; the identical command later resumed the unattempted next row and completed all
30 without rerunning a finished outcome.

Read the [full result and interpretation](../../../docs/FIX_COVERAGE_RESULTS.md) and the
sanitized [`public-report.json`](./public-report.json). Raw dependency reasons, Cases,
generated tests, provider diagnostics, and logs remain private.
