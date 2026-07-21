---
layout: default
title: Evidence strength scalar
---

# Evidence strength scalar

`evidence-strength/v1` is a descriptive ranking metric for evidence that has already
cleared the deterministic judge. It never admits evidence, changes a verdict, or
converts an unavailable measurement into a zero. `flip_check.py` remains the sole
authority for `PROVEN`, `REPRODUCED`, and `INSUFFICIENT_EVIDENCE`.

## Components and initial weights

| Component | Weight | v1 normalization |
| --- | ---: | --- |
| Mutation kill rate | 30% | killed / eligible deterministic mutants; invalid mutants excluded |
| Failure-signature specificity | 20% | typed exception + value `1.0`; typed exception `0.8`; assertion + expression `0.65`; bare assertion `0.4` |
| Determinism | 20% | stable target reruns / 5, capped at `1.0`; instability is `0` |
| Minimality | 15% | `min(6 / verified_minimized_lines, 1.0)` |
| Test-to-root-cause distance | 15% | `1 / (1 + intervening repository frames)` |

The composite is the weighted mean of available components. `coverage` is the sum of
their available weights, from `0.0` to `1.0`. For example, a composite of `1.0` at
`0.55` coverage means every measured dimension was strong—not that all dimensions
were measured. Rankings should compare Cases with similar coverage until the weights
are calibrated against human judgments.

## Measurement boundaries

Mutation discovery is restricted to source paths grounded by the PR diff, failure
trace, or generated test's repository-local imports. Mutants run against the passing
state using disposable executor copies. If no allowlisted mutant exists, the mutation
component is unavailable rather than zero.

Source distance requires repository frames in the captured failure trace. An imported
module can establish a mutation surface but cannot establish call distance; value
mismatch assertions often expose only the test frame, so this component may honestly
remain unavailable. Minimality likewise requires an independently reverified shrink.

The metric is a research hypothesis, not a calibrated probability that a report is a
bug. Its schema version, component scores, weights, human-readable bases, composite,
and coverage are stored together in `Case.evidence_strength` so later validation can
recompute or replace the formula without rewriting history.
