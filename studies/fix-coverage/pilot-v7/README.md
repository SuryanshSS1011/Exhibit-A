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
