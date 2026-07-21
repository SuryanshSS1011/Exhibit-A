# Operational contract

## Trigger policy

Exhibit A is deliberately on-demand. Integrations call
`exhibit_a.operations.should_trigger` and start an investigation only for:

- an issue/PR comment containing `/exhibit-a review` as its own line; or
- the pull request `ready_for_review` transition.

`synchronize`, ordinary pushes, and general comments return false. A multi-minute,
model-backed investigation is not economically viable on every pushed commit.

## Suite-gap reporting

The engine runs the explicitly configured existing suite once as a preflight in the
same disposable executor. An integration can also attach a trusted external CI result
via `annotate_suite_gap(case, existing_suite_passed=...)`:

> `suite_gap = Exhibit A produced evidence AND the existing suite passed`

This measures the incremental bugs Exhibit A exposes rather than taking credit for
failures CI already catches. The headline quality metric is
`semantic_precision(human_labels)`: human-confirmed regressions divided by all
human-judged flagged deltas. Raw flip rate is not a semantic-precision metric.

The separate `self-audit` command measures the complementary failure mode: speaking on
validated behavior-preserving refactors. It reports false convictions with Wilson 95%
intervals rather than treating a small 0/N sample as perfect precision.

## After PROVEN

The generated test ships as a Case File artifact or review suggestion. Exhibit A
does not commit it, push it, or turn it into an automatic patch. A maintainer first
reviews the deterministic delta and the separately labeled intent assessment, then
decides whether the behavior is a regression and who owns the test. Only after that
human decision should the test enter the repository alongside its fix.
