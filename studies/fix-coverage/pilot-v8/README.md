# Fix-coverage pilot v8

Pilot v8 asks the question v7 could not answer: do the engine changes derived from v6's
failures generalize to repositories nobody looked at while making them?

v7 measured 7/30 VERIFIED on a corpus that is instance-disjoint from v5 and v6, but it
shares 17 of its 21 repositories with v6, and five of its seven verified instances come
from repositories the changes were fitted to. That is a sound measurement of the product
and cannot support a generalization claim.

v8 holds the date window, the search rules, the environment precedence, the budgets and
the taxonomy fixed, and moves one variable: no repository named by v5, v6 or v7 may be
selected at all. Exclusion happens before the clone and before eligibility, so a used
repository costs nothing and does not consume a slot in the retained frame.

The method requires a provider-free `--probe` over the final corpus before any provider
run. If fewer than 3 instances reach the deterministic judge, v8 stops without a model
call and publishes that plumbing result. Otherwise the probe report is frozen and the
provider run uses the identical corpus with minimization disabled. Environment-install
failures are measured and classified but not fixed during this pilot.

The preregistration was committed before selection, probe, provider calls, container
builds, test executions, Cases, or verdicts. The mechanical selector then froze 30 fixes
across 17 repositories, screening 333 star-ranked repositories and skipping 29 as prior
corpus members. The hash-pinned [`corpus.json`](./corpus.json) and
[`selection-report.md`](./selection-report.md) preserve every exclusion.

Results are scoped to the recorded platform. Every pilot so far has run on `linux/arm64`,
and that is recorded in the report rather than assumed away.
