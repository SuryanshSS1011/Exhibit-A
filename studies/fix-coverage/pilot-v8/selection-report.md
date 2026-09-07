# Pilot v8 frozen corpus

Pilot v8 reached the preregistered 30-instance target before any reach probe, provider
call, container build, candidate execution, Case, or verdict. It changes exactly one rule
from v7: a repository named by the v5, v6 or v7 corpus is excluded **whole**, before its
clone and before its pinned-environment eligibility is evaluated, rather than only its
individual pull requests.

| Selection fact | Count |
|---|---:|
| Star-ranked repositories screened | 333 |
| Repositories excluded as prior-corpus members | 29 |
| Environment-ineligible repositories | 254 |
| Environment-eligible repositories retained | 50 |
| Retained repositories with fresh fix candidates | 29 |
| Included fresh instances | 30 |
| Repositories represented in the corpus | 17 |
| Candidates excluded by revision/environment checks | 48 |
| Candidates outside the five-per-repository cap | 635 |
| Eligible candidates remaining after the target sample filled | 54 |

The 48 ordinary candidate-level eligibility exclusions comprise 32 candidates with no
production Python change and 16 with an unsupported or invalid pinned environment on at
least one revision. The other 689 exclusions are mechanical boundaries rather than failed
Exhibit A runs: 635 exceeded the repository cap and 54 remained after the target sample
was full. No candidate was excluded as a prior-corpus member, because every repository
those PRs belong to was already gone.

## Why the frame was widened, and by how much

v7 screened 192 repositories to retain 50 eligible, so roughly one in four passes the root
pinned-environment parser. Removing the 29 repositories v5, v6 and v7 had consumed meant
screening further down the star ranking to refill the frame. The preregistration estimated
"on the order of 300" and registered a 600 limit as headroom; selection screened **333**
and stopped on `target_reached`. The registered limit was never approached, so no
pre-execution amendment was needed and none was made.

## Disjointness

The corpus shares no repository with v5, v6 or v7, and therefore no instance either. This
is the property v7 lacked: v7 is instance-disjoint from v6 while sharing 17 of its 21
repositories with it, and five of its seven verified instances came from repositories whose
failures the engine changes under test were derived from. v8 is the first corpus whose
repositories were never examined while building those changes, which is what makes its
verified fraction usable as evidence of generalization.

Thirty instances span 17 repositories, at most three from any one, well inside the
five-per-repository cap.
