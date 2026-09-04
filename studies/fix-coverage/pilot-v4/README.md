# Fix-coverage pilot v4

Pilot v4 is identical to v3 except for bounded retries on transient GitHub metadata reads.
The v3 public record contains 50 `No route to host` exclusions and 16 included instances;
no Exhibit A outcome existed when v4 was committed.

Five metadata attempts with fixed 1, 2, 4, and 8 second backoffs prevent a brief transport
outage from masquerading as corpus ineligibility. This does not relax or retry any model,
container, test, or verdict outcome.

The resulting 30-instance corpus was frozen before execution. See
[`selection-report.md`](./selection-report.md) and [`corpus.json`](./corpus.json).

The completed run reached **1/30 VERIFIED (3.3%)** and **0/30 PARTIAL**. The checked-in
[`public-report.json`](./public-report.json) contains log-free per-instance outcomes and
both the preregistered and refined taxonomies. The detailed interpretation is preserved in
[`docs/FIX_COVERAGE_V4_RESULTS.md`](../../../docs/FIX_COVERAGE_V4_RESULTS.md). Raw Cases and logs
remain private.
