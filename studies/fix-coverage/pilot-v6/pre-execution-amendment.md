# Pilot v6 pre-execution amendment: make “fresh corpus” disjoint

Recorded after the first mechanical v6 selection and before any provider call, container
build, test execution, Case, or verdict. The first selection used the marker-aware v6 code
but selected the same 30 PR IDs as v5. Its original bytes had SHA-256
`958eaba7f1a7af457ebea342c915082b102cca725fe61f6b7216c717ed4d1577`.
The result is preserved as `initial-overlap-selection.json`; the added
`selection_disposition` field documents why it is not the execution corpus.

A timestamp-only manifest containing the same instances does not satisfy the user's
explicit requirement to select a fresh corpus. The selector therefore gains one mechanical
input: `--exclude-manifest`. For v6 it is the frozen v5 corpus, SHA-256
`b95789fa86a7c26268d8b237e9fe6656a77e661525536abecc332fcbbe653f58`.
Matching PR source URLs are excluded before the five-per-repository cap, and every exclusion
is recorded as `prior_corpus_member`. Selection then continues in the same registered
repository, PR, date, and round-robin order until it reaches 30 or exhausts the universe.

This amendment supersedes only the preregistration sentence that declined to force
disjointness. It changes no environment eligibility rule, repository frame, date window,
claim, model, budget, failure taxonomy, analysis threshold, Case, or verdict logic. It is
committed with the selector code before the replacement corpus is selected. Because no
outcome existed, the amendment cannot be informed by Exhibit A performance.
