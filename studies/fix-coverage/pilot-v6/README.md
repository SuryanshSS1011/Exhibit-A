# Fix-coverage pilot v6

Pilot v6 is a new selection and run under the corrected `uv.lock` graph semantics. It is
not a rerun of the hash-pinned v5 corpus. The public GitHub/cache frame and the mechanical
selection rule are held constant where possible, while environment eligibility is rerun
with workspace-root reachability, dependency-edge markers, and `resolution-markers`.

The preregistration is committed before selection, provider calls, container builds, test
executions, or v6 outcomes. It makes judge reach the primary metric, fixes the comparison
against v5's two marker-sensitive dependency categories in advance, requires the measured
Docker platform beside the dependency breakdown, and forbids outcome-based exclusions.

Selection and result artifacts will be added only after their corresponding frozen steps.

The first outcome-free selection returned the same 30 PR IDs as v5. The preserved
[`initial-overlap-selection.json`](./initial-overlap-selection.json) and
[`pre-execution-amendment.md`](./pre-execution-amendment.md) record why v6 mechanically
excludes the frozen v5 manifest before applying the repository cap. The replacement corpus
will remain governed by every other preregistered selection rule.

The amended selector then froze 30 PRs with no v5 overlap. The hash-pinned
[`corpus.json`](./corpus.json) and [`selection-report.md`](./selection-report.md) record the
21 represented repositories, every exclusion, and the 27 `uv.lock` plus three Poetry
instances. No v6 execution outcome existed when this selection was committed.

The completed run reached the judge on **2/30 instances (6.7%)** and VERIFIED both; 0/30
were PARTIAL. Dependency installation blocked 17/30 on the recorded Docker `linux/arm64`
platform. `pinned_distribution_unavailable` fell from 7 in v5 to 3 in v6, and
`dependency_resolution_conflict` fell from 4 to 2. Because the corpora are disjoint, those
changes are descriptive rather than causal.

Read the [historical full result and interpretation](../../../docs/FIX_COVERAGE_V6_RESULTS.md), the
sanitized [`public-report.json`](./public-report.json), and the
[`post-run-reporting-amendment.md`](./post-run-reporting-amendment.md) that records three
reporting-only provenance corrections made before public export. Raw dependency reasons,
Cases, generated tests, provider diagnostics, and logs remain private.
