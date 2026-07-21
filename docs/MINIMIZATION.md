---
layout: default
title: Verified evidence minimization
---

# Verified evidence minimization

Evidence minimization is an optional post-verdict pass for full `PROVEN` flips. It
reduces a generated pytest reproduction only when the smaller candidate independently
clears the same deterministic `flip_check` as the original:

- fails on every target rerun;
- passes on the base/fixed state;
- retains the expected failure signature;
- passes the control state when one was supplied; and
- still satisfies the tamper, vacuity, infrastructure, and changed-line gates.

The bounded minimizer first applies line-level delta debugging, then proposes
single-use setup-value inlining and smaller literal/container inputs. Syntax checks
can reject malformed proposals cheaply, but only execution can accept a proposal.
Every executor run still uses its disposable checkout; source trees are never edited.

For a successfully reverified pass, `Case.test_file` contains the smallest artifact
found and `Case.original_test_file` preserves the generator output. `Case.minimization`
records the attempt budget, accepted transformations, line counts, reduction ratio,
and independent-verification status. These are descriptive provenance and future
minimality inputs—not evidence admission criteria.

If minimization errors or cannot independently verify its output, the already-proven
original remains `Case.test_file`. The raw `PROVEN` verdict and its original execution
logs are unchanged in either case. `verdict/flip_check.py` remains the sole judge.
