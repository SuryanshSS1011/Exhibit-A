---
layout: default
title: Mutation-testing foundation
---

# Mutation-testing foundation

`exhibit_a.verdict.mutation_testing` measures how strongly a generated test
constrains suspect code. It is deliberately a scoring layer, not an evidence gate:
`flip_check.py` remains the only module that can admit a Case.

## Process

1. `discover_mutations` tokenizes caller-selected Python source files and emits a
   deterministic ordered set of allowlisted operator replacements. Optional suspect
   line sets narrow the surface; input paths are repository-relative and cannot
   traverse outside the checkout.
2. `score_mutations` reruns the candidate on the unmodified pass-state baseline. If
   that baseline is not deterministically green, no kill rate is reported.
3. Each mutant is independently applied to a fresh disposable executor copy. Docker
   execution retains the no-network, dropped-capability, read-only-container policy.
4. A mutant is `killed` only when every run fails with the same extractable signature,
   `survived` only when every run passes, and `invalid` when unsupported, flaky,
   timed out, or broken at the harness/environment layer.

The score reports `killed / (killed + survived)`. Invalid mutants remain visible but
are excluded from the denominator. No score can upgrade, downgrade, or fabricate the
raw `PROVEN` / `REPRODUCED` / `INSUFFICIENT_EVIDENCE` verdict.

## Initial operator set

- equality and ordering boundaries: `==`, `!=`, `<`, `<=`, `>`, `>=`
- arithmetic operators: `+`, `-`, `*`, `//`, `%`
- Boolean literals: `True`, `False`

These are syntax-preserving token edits rather than model-generated patches. The
executor independently rejects any replacement outside the allowlist, even if a
caller constructs a `SourceMutation` directly. The current foundation targets UTF-8
Python source and requires callers to identify suspect files; broader language and
mutation-operator studies belong in later research commits.
