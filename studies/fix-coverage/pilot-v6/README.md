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
