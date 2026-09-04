# Pilot v4 frozen corpus

Pilot v4 reached the preregistered target before any Exhibit A outcome was observed.

| Selection fact | Count |
|---|---:|
| Star-ranked repositories screened | 490 |
| Environment-ineligible repositories | 440 |
| Environment-eligible repositories retained | 50 |
| Retained repositories with fix candidates | 19 |
| Included instances | 30 |
| Repositories represented in the corpus | 15 |
| Candidates excluded by revision/environment checks | 20 |
| Eligible candidates outside the target sample | 31 |
| Candidates outside the five-per-repository cap | 360 |

The 30 instances span February 17 through August 21, 2026. All 30 satisfied the
preregistered title-prefix rule; one also carried an exact `bug` label. Round-robin order
limited every represented repository to at most three of the 30 instances.

The immutable corpus SHA-256 is
`b293d97d3a3f786339bc964460562b0824aec960323c6f8fe84406bddae26cd4`.
The complete repository frame, every candidate exclusion, pinned revisions, claims, dates,
and selection bases are in `corpus.json`. No home path, username, token, model output, test
execution, or verdict is present.
