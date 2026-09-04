# Pilot v6 frozen corpus

Pilot v6 reached the amended 30-instance target before any Exhibit A outcome was observed.
It reran marker-aware environment eligibility over the held-constant v5 public cache frame,
then mechanically excluded every PR in the frozen v5 corpus before applying the
five-per-repository cap.

The first outcome-free selection selected all 30 v5 PR IDs again. It is preserved in
`initial-overlap-selection.json`, and the reason for requiring disjointness is recorded in
`pre-execution-amendment.md`. No model call, container build, test, Case, or verdict existed
when the amendment and selector change were committed.

| Selection fact | Count |
|---|---:|
| Star-ranked repositories screened | 192 |
| Environment-ineligible repositories | 142 |
| Environment-eligible repositories retained | 50 |
| Retained repositories with fresh fix candidates | 38 |
| Prior v5 PRs excluded before the repository cap | 30 |
| Included fresh instances | 30 |
| Repositories represented in the corpus | 21 |
| Candidates excluded by revision/environment checks | 31 |
| Eligible candidates outside the target sample | 110 |
| Candidates outside the five-per-repository cap | 868 |

The retained repository environments resolve through 33 `uv.lock` files, seven
`poetry.lock` files, seven `requirements*.txt` sets, and three `Pipfile.lock` files. In the
fresh selected corpus, 27 instances use `uv.lock` on both revisions and three use
`poetry.lock` on both. No selected instance changes environment source between its buggy
and fixed revisions.

The 30 instances span February 17 through August 16, 2026. Twenty-two qualified by the
registered fix-title prefix alone, six by both that prefix and an exact `bug` label, and
two by the exact label alone. Round-robin ordering limited every represented repository to
at most two instances.

The 31 ordinary eligibility exclusions comprise 18 candidates with no production Python
change and 13 with an unsupported or invalid pinned environment on at least one revision.
The remaining exclusions are mechanical boundaries, not failed Exhibit A runs: 30 belong
to v5, 868 were beyond the five-per-repository cap, and 110 remained after the target sample
was full.

The immutable execution corpus SHA-256 is
`64ca53193d5138200b98d21363cfd178e14fb49c8b87381d32a86e5026035aa8`.
The complete repository frame, every exclusion, pinned revisions, claims, dates, and
selection bases are in `corpus.json`. No home path, username, token, model output, test
execution, or verdict is present.
