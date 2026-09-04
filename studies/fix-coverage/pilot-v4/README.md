# Fix-coverage pilot v4

Pilot v4 is identical to v3 except for bounded retries on transient GitHub metadata reads.
The v3 public record contains 50 `No route to host` exclusions and 16 included instances;
no Exhibit A outcome existed when v4 was committed.

Five metadata attempts with fixed 1, 2, 4, and 8 second backoffs prevent a brief transport
outage from masquerading as corpus ineligibility. This does not relax or retry any model,
container, test, or verdict outcome.

The resulting 30-instance corpus was frozen before execution. See
[`selection-report.md`](./selection-report.md) and [`corpus.json`](./corpus.json).
