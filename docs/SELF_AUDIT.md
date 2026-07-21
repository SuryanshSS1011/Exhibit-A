# Adversarial self-audit

The `self-audit/v1` harness measures whether Prosecutor falsely speaks on deliberately
correct, behavior-preserving refactors. The initial checked-in corpus contains helper
rename, extract-method, and loop-to-comprehension pairs. Each pair includes a behavior
contract that must pass on both states before the item enters the denominator.

From `engine/`:

```bash
python3 -m exhibit_a.cli self-audit ../fixtures/refactor_corpus --offline
python3 -m exhibit_a.cli self-audit ../fixtures/refactor_corpus --docker
```

The first command is a fast plumbing check with the deterministic stub. The second uses
the live Codex generator and hardened executor. Reports default to
`.exhibit-a/research/self-audit` and include every raw Case, invalid-corpus reason, and
per-category result. They are private research records; claims and model output may be
sensitive.

Any `PROVEN` Case on a validated behavior-preserving pair is a false conviction,
regardless of its later intent label. The report shows the observed rate and a two-sided
Wilson 95% interval. Small clean corpora retain wide upper bounds: 0/3 is not evidence
of perfect precision.

The web dashboard is also private by default. Set
`EXHIBIT_A_RESEARCH_DASHBOARD=1` in a trusted local environment to show the latest
report. `EXHIBIT_A_SELF_AUDIT_DIR` can point it at a different report directory.

This corpus is a foundation, not a publishable benchmark result. Expand categories,
languages, repository shapes, and sample size; pre-register the analysis; and have
humans verify that every transform truly preserves its declared public behavior before
making false-conviction claims.
