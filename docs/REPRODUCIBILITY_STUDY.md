# Reproducibility-of-reproduction study

`reproducibility-study/v1` repeats the same claim through fresh Exhibit A engines and
measures whether independent samples converge on the same terminal verdict, exercised
root cause, and conservatively equivalent test. It is private research instrumentation,
not another evidence gate: each Case still receives its verdict only from
`verdict/flip_check.py`.

## Run it

From `engine/`, a five-sample live study against the dependency-free demo bug is:

```bash
python3 -m exhibit_a.cli study ../fixtures/buggy_inventory \
  --fixed ../fixtures/fixed_inventory \
  --claim "stock_for should return zero for an unknown SKU instead of raising KeyError" \
  --expect KeyError \
  --runs 5
```

Cycle model variants by repeating `--model`:

```bash
python3 -m exhibit_a.cli study ../fixtures/buggy_inventory \
  --fixed ../fixtures/fixed_inventory \
  --claim "stock_for should return zero for an unknown SKU instead of raising KeyError" \
  --expect KeyError --runs 6 \
  --model gpt-5.6-sol --model gpt-5.6-terra
```

The Codex CLI does not expose a stable sampling-seed contract, so repeated calls to one
model are recorded as independent samples, not falsely labeled seeded experiments.
`--offline` runs the deterministic stub as a fast pipeline smoke test. `--docker` uses
the hardened executor. The initial command intentionally supports local pinned
checkouts; callers should make both directories immutable snapshots of the same states.

## What is measured

- **Verdict convergence:** modal verdict count / completed samples.
- **Root-cause convergence:** modal fingerprint count / samples with executable
  evidence. The fingerprint combines repository-imported call targets with the observed
  failure type; test-framework and standard-library calls are excluded.
- **Test-semantic convergence:** modal fingerprint count / samples with executable
  evidence. Python ASTs ignore formatting, comments, test-function names, and local
  variable names while preserving imported targets, assertion structure, and literal
  inputs. This is a conservative equivalence proxy: two tests with different concrete
  inputs remain different even when a human might generalize them to one property.

Every metric reports coverage separately. Strict convergence requires all requested
runs to complete and both executable-evidence fingerprints to have full modal agreement.
Consistent `INSUFFICIENT_EVIDENCE` is therefore a valuable negative result with perfect
verdict convergence, but it is not mislabeled reproduction convergence.

## Output and interpretation

Reports default to `.exhibit-a/research/reproducibility/<study-id>.json` and contain the
versioned formula, model variants, convergence groups, errors, fingerprints, and full
raw Cases for auditability. This directory is private by default: claim text, generated
tests, repository paths, and execution logs may be sensitive. Publish only redacted,
consented datasets with engine/model/date provenance.

Divergence is a study result, not a command failure. The CLI exits nonzero only for an
invalid setup or an unwritable report. Runtime and model cost scale approximately with
K; every `PROVEN` sample still performs the real bounded minimization and strength
measurements. Start with K=5 for exploration and pre-register a larger K and analysis
plan before making research claims.
