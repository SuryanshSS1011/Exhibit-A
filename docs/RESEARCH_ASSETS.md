---
layout: default
title: Private research assets
---

# Private research assets

Exhibit A turns routing exhaust into three versioned, local datasets. They live
under `engine/.exhibit-a/research/`, have no public read endpoint, and are never
posted to a pull request.

- `intent-confirmations/` records a human `intended` / `regression` label from the
  neutral **BEHAVIOR CHANGE** prompt alongside the raw verdict, model, declared
  behavior delta, engine version, and date.
- `observatory/` registers every minted full-flip Case. A scheduler can invoke
  `exhibit-a observe` with a freshly resolved, validated upstream SHA. The exact
  generated test runs in the Docker executor; the record appends `healthy`,
  `re_regressed`, `flaky`, or `inconclusive` plus raw runs and the next due date.
- `flaky/` quarantines candidates rejected by the determinism gate, including the
  generated test, raw runs, environment reference, engine/model versions, and date.

Example scheduled observatory invocation:

```bash
python3 -m exhibit_a.cli observe .exhibit-a/cases/CASE.json \
  https://github.com/org/repo.git --upstream-sha 0123456789abcdef \
  --out .exhibit-a/research
```

The scheduler is responsible for resolving upstream `main` to a SHA. Exhibit A
accepts only that validated SHA and an HTTPS URL; it never widens Git input rules to
accept an untrusted branch expression. Observatory and human labels route research
records only. Neither can change a Case verdict.
