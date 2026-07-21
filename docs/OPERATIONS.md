# Operational contract

## Trigger policy

Exhibit A is deliberately on-demand. Integrations call
`exhibit_a.operations.should_trigger` and start an investigation only for:

- an issue/PR comment containing `/exhibit-a review` as its own line; or
- the pull request `ready_for_review` transition.

`synchronize`, ordinary pushes, and general comments return false. A multi-minute,
model-backed investigation is not economically viable on every pushed commit.

## Suite-gap reporting

The engine still executes only its generated test. It never broadens scope to run
the repository suite. An integration supplies the existing CI result afterward via
`annotate_suite_gap(case, existing_suite_passed=...)`:

> `suite_gap = Exhibit A produced evidence AND the existing suite passed`

This measures the incremental bugs Exhibit A exposes rather than taking credit for
failures CI already catches. The headline quality metric is
`semantic_precision(human_labels)`: human-confirmed regressions divided by all
human-judged flagged deltas. Raw flip rate is not a semantic-precision metric.

## After PROVEN

The generated test ships as a Case File artifact or review suggestion. Exhibit A
does not commit it, push it, or turn it into an automatic patch. A maintainer first
reviews the deterministic delta and the separately labeled intent assessment, then
decides whether the behavior is a regression and who owns the test. Only after that
human decision should the test enter the repository alongside its fix.
