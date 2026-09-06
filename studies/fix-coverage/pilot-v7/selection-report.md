# Pilot v7 frozen corpus

Pilot v7 reached the preregistered 30-instance target before any reach probe, provider
call, container build, candidate execution, Case, or verdict. Selection reused the fixed
public GitHub/cache frame, reran current environment eligibility, and mechanically
excluded every PR URL in the hash-pinned union of the v5 and v6 corpora before applying
the five-per-repository candidate cap.

| Selection fact | Count |
|---|---:|
| Star-ranked repositories screened | 192 |
| Environment-ineligible repositories | 142 |
| Environment-eligible repositories retained | 50 |
| Retained repositories with fresh fix candidates | 36 |
| Prior v5/v6 PRs excluded before the repository cap | 60 |
| Included fresh instances | 30 |
| Repositories represented in the corpus | 21 |
| Candidates excluded by revision/environment checks | 42 |
| Candidates outside the five-per-repository cap | 842 |
| Eligible candidates remaining after the target sample filled | 95 |

The 42 ordinary candidate-level eligibility exclusions comprise 28 candidates with no
production Python change and 14 with an unsupported or invalid pinned environment on at
least one revision. The other 997 exclusions are mechanical boundaries rather than
failed Exhibit A runs: 60 belong to v5 or v6, 842 exceeded the repository cap, and 95
remained after the target sample was full.

The selected corpus contains 27 instances using `uv.lock` on both revisions, two using
`poetry.lock` on both, and one using pinned `requirements*.txt` on both. No selected
instance changes environment source between its buggy and fixed revisions.

The 30 fixes span February 17 through August 16, 2026. Twenty-five qualified by the
registered fix-title prefix alone and five by both that prefix and an exact `bug` label.
Round-robin ordering limited every represented repository to at most two instances.

The amended execution corpus SHA-256 is
`f0cc23ecd53c4289da248904a9f38f90de56a9f7613f4e7c80704fe0d2d74282`.
The originally frozen corpus hash is preserved in `amendment-001.json`. The complete
repository frame, every exclusion, pinned revision, claim, date, and selection basis are
in `corpus.json`. No probe or provider outcome influenced the selection.
# Administrative amendment before execution

The first probe invocation stopped while parsing the corpus, before cloning or attempting
any instance. The selector had generated `plotly-plotly.py-pr-5517`, but the runner's ID
grammar excludes periods. The ID was normalized to `plotly-plotly-py-pr-5517`; the same
PR remains selected in the same position with the same claim and revisions. The original
hash and the zero-observation timing are recorded in
[`amendment-001.json`](./amendment-001.json).
