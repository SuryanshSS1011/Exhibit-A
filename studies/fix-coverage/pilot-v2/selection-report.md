# Pilot v2 selection result

The amended selection still did not reach the registered 30-instance pilot. It produced
five instances, all from one repository, before any model call, container build, or
Exhibit A verdict. No coverage fraction is reported from this short corpus.

| Selection result | Count |
|---|---:|
| Star-ranked repositories screened | 490 |
| Repositories rejected by pinned-environment eligibility | 440 |
| First environment-eligible repositories retained | 50 |
| Eligible repositories with no exact-`bug`-label PR in the window | 49 |
| Eligible repositories with matching PRs | 1 |
| Included instances under the five-per-repository cap | 5 |
| Matching candidates outside that cap | 53 |

The full repository and candidate records are in `corpus.json`. The result reveals two
independent selection constraints: only 10.2% of the first 490 star-ranked Python
repositories fit the current environment loader, and exact PR-level `bug` labels are
concentrated enough that 49 of those 50 repositories contributed nothing.

The next amendment broadens only the mechanical bug indicator to accommodate repositories
that encode the same information in PR titles rather than labels. Runtime eligibility,
dates, repository frame, sample size, and all outcome rules remain unchanged.
