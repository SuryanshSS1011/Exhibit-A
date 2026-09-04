# Pilot v3 selection result

Pilot v3 produced 16 eligible instances rather than the registered 30 and was not run as
the verdict pilot. It still observed no model or Exhibit A outcome.

| Selection result | Count |
|---|---:|
| Star-ranked repositories screened | 490 |
| Environment-ineligible repositories | 440 |
| Environment-eligible repositories retained | 50 |
| Repositories with mechanically signaled fix candidates | 19 |
| Repositories without such candidates | 31 |
| Included instances | 16 |
| Candidates outside the five-per-repository cap | 360 |
| Candidate metadata requests lost to a transient network outage | 50 |
| No production Python change | 8 |
| Revision no longer matched the pinned-environment rule | 6 |
| Git revision resolution failed | 1 |

All 16 included instances came from the title-prefix rule. The 50 metadata failures were
`No route to host` errors after the search frame had been fixed; treating those as if the
underlying fixes were ineligible would confound network availability with corpus scope.
The next preregistered attempt therefore keeps the v3 population and candidate rule
unchanged and adds bounded transport retries only. The raw selector output, including one
path-bearing Git diagnostic, remains private; the public corpus changes only that detail.
