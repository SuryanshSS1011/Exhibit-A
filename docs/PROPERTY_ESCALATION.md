---
layout: default
title: Property-based escalation
---

# Property-based escalation

After a concrete Case is PROVEN, Exhibit A may ask Codex to generalize its single
example into a broader property. The generator works read-only and may return either a
Hypothesis `@given` test when that dependency already exists or a pytest parametrization
with at least three explicit examples. It may also decline.

```bash
cd engine
python3 -m exhibit_a.cli property case.json /path/to/buggy \
  --fixed /path/to/fixed --source package/module.py
```

The property candidate passes the normal test-path, command, tamper, vacuity,
infrastructure, signature, determinism, and fail-to-pass gates in `flip_check.py`.
Optional `--source` paths run the existing disposable mutation score against the
property on the fixed state. The report retains the concrete Case's descriptive strength
score alongside the property mutation kill rate; v1 does not combine or recalibrate
them.

Reports use `property-escalation/v1`. `verified_property_flip` means the generalized
test itself produced a real flip over the declared domain. The original concrete test
remains canonical Case evidence. A declined or rejected escalation cannot weaken it, and
a verified property cannot override the original verdict.

Pytest parametrization is a bounded domain sample, not universal quantification.
Hypothesis coverage is likewise governed by its configured search strategy. The report's
`property_kind` and human-readable `domain` keep that distinction explicit.
