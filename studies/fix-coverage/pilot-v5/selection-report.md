# Pilot v5 frozen corpus

Pilot v5 reached the preregistered 30-instance target before any Exhibit A outcome was
observed. It reran environment eligibility over the held-constant v4 repository frame with
the committed `uv.lock` selector integration.

| Selection fact | Count |
|---|---:|
| Star-ranked repositories screened | 192 |
| Environment-ineligible repositories | 142 |
| Environment-eligible repositories retained | 50 |
| Retained repositories with fix candidates | 40 |
| Included instances | 30 |
| Repositories represented in the corpus | 25 |
| Candidates excluded by revision/environment checks | 21 |
| Eligible candidates outside the target sample | 126 |
| Candidates outside the five-per-repository cap | 892 |

The retained repository environments resolve through 33 `uv.lock` files, seven
`poetry.lock` files, seven `requirements*.txt` sets, and three `Pipfile.lock` files. In the
selected corpus, 22 instances use `uv.lock` on both revisions, four use `poetry.lock` on
both, and four use `requirements*.txt` on both. No selected instance changes environment
source between its buggy and fixed revisions.

Under v4's older eligibility rule, finding 50 accepted repositories required screening
490; v5 requires 192, a 2.55× improvement in screening yield. Six selected PRs overlap the
v4 corpus and 24 are new. This comparison describes selection reach only—no build, model,
test, judge, or verdict outcome had occurred when this report was written.

The 30 instances span February 17 through July 20, 2026. Twenty-seven qualified by the
registered fix-title prefix alone, two by both the prefix and an exact `bug` label, and one
by the exact label alone. Round-robin ordering limited every represented repository to at
most two instances.

The 21 eligibility exclusions comprise 12 candidates with no production Python change and
nine with an unsupported or invalid pinned environment on at least one revision. The other
1,018 exclusions are sampling boundaries, not failed Exhibit A runs: 892 were beyond the
five-per-repository cap and 126 remained after the target sample was full.

The immutable corpus SHA-256 is
`b95789fa86a7c26268d8b237e9fe6656a77e661525536abecc332fcbbe653f58`.
The complete repository frame, every exclusion, pinned revisions, claims, dates, and
selection bases are in `corpus.json`. No home path, username, token, model output, test
execution, or verdict is present.
