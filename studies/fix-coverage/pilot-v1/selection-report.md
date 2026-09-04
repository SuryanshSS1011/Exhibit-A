# Pilot v1 selection result

The preregistered September 4, 2026 selection did not produce a runnable corpus.
This is a sampling-frame result, not a 0% VERIFIED result: with zero included instances,
there is no verdict denominator.

| Selection result | Repositories |
|---|---:|
| No supported root lock input | 35 |
| Root dependency input present but not fully pinned/accepted | 10 |
| Accepted environment shape, but no matching post-cutoff `bug` PR | 4 |
| Clone failed | 1 |
| Produced an eligible instance | 0 |

All 50 star-ranked repositories and their individual reasons are retained in
`corpus.json`. One public clone diagnostic is deliberately path-redacted; the byte-for-byte
raw selector output is retained privately. No Exhibit A investigation, model call, Docker
build, or verdict occurred before this result was recorded.

The finding is already important: the current root-only, fully pinned environment loader
does not cover 90% of the initially selected high-star Python repositories. Expanding the
repository scan without changing eligibility is necessary to obtain a verdict pilot. That
protocol change must be preregistered and committed separately; this file and corpus remain
the immutable record of why.
