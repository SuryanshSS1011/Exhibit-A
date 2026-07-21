<div align="center">

# Exhibit A

**An evidence engine for code that is only allowed to speak with proof.**

*Every claim it makes is a rerunnable test: red on the bug, green on the fix. When it cannot prove one, it stays silent.*

*Every proof it produces is also open data for AI-for-software-engineering (AI4SE) research.*

</div>

---

## The problem

AI code reviewers have a trust problem. They cry wolf. A bot that flags ten "issues" with
eight of them noise trains developers to skim past all ten, and the one real bug ships.
The failure mode is not missing bugs. It is alert fatigue that collapses trust in the tool.

Every mainstream reviewer optimizes for catch rate and reports a confidence score.
Confidence is not proof. A 90%-confident wrong comment is still a wrong comment.

## The idea

Exhibit A inverts the contract. It is governed by **one rule**:

> It may only report a bug if it can hand you a **runnable test that fails on the broken
> code and passes on the fix.** No proof, no comment. When it cannot prove a suspicion, it
> stays **silent** and records why.

This is enforced by construction rather than by a threshold. A deterministic, model-free
**flip check** is the sole judge of what counts as evidence, and it trusts execution logs
over anything the model claims. The result is a reviewer whose every statement is backed
by an artifact you can re-run in seconds, and whose silence is a feature rather than a
failure.

The same discipline produces a second output. Because every proof is an
execution-validated fail-to-pass test tied to a specific commit, each one is a
ready-made **benchmark instance**. Exhibit A doubles as a minting press for the
contamination-free datasets that AI4SE research needs, and it
emits research-grade artifacts as a byproduct of doing its day job.

## Two modes, one engine

| Mode | Input | Output |
|------|-------|--------|
| **Detective** | A stack trace, error, or bug report plus a repo | An autonomously reproduced, verified fail-to-pass test |
| **Prosecutor** | A pull request | A review comment only when a flip is proven |

Both run on the shared **Evidence Engine**:

```
claim + code state(s)
    -> hypothesize (Codex / GPT-5.6, read-only)
    -> generate candidate test (pass-then-invert)
    -> execute both states in a sandbox
    -> FLIP CHECK  (deterministic, no model)
    -> PROVEN Case File   or   INSUFFICIENT_EVIDENCE (Silence Log)
```

## What it proves, and what it does not

The flip check proves that behavior **changed** between two states. It does not prove the
change is a **bug**, since most changes are intentional. A separate intent step labels a
proven change as a regression or an expected one, and that label never overrides the
execution result.

Verdicts are tiered so the tool never overclaims:

| Verdict | Meaning |
|---------|---------|
| `PROVEN` | Fails on the broken code, passes on the fix. A full flip. |
| `REPRODUCED` | A deterministic, signature-matched failure with no known-good state to compare against. |
| `INSUFFICIENT_EVIDENCE` | Nothing cleared the gate. Honest silence. |

**Scope:** deterministic functional bugs in Python repos that build in a sandbox. It
cannot speak to race conditions, performance regressions, or most security issues, and it
stays silent instead of guessing.

## Open science

The evidence discipline that makes Exhibit A trustworthy also makes it a data engine.
Every verified Case is an execution-validated fact about real code, and the project turns
those facts into open research assets.

- **Contamination-free benchmarks.** Each `PROVEN` Case carries a commit SHA, a
  fail-to-pass test, and a date, which is exactly the shape of a SWE-bench-style instance.
  Because instances are minted continuously from live fixes and tagged by date, they can
  be filtered against any model's training cutoff, so the benchmark does not rot into the
  training set.
- **Signed, replayable evidence bundles.** A Case can be exported as a self-contained
  bundle (pinned commits, the test, the run command, logs, and content hashes) that anyone
  can re-execute and verify offline. See [`docs/EEF.md`](./docs/EEF.md).
- **Negative results as a dataset.** The Silence Ledger records what the engine suspected
  but could not prove. Nobody publishes what reproduction tools fail to reproduce, which
  makes this a genuinely novel research asset. See
  [`docs/RESEARCH_ASSETS.md`](./docs/RESEARCH_ASSETS.md).
- **Auditing the benchmarks themselves.** The same mutation machinery measures how strong
  a benchmark's own tests are, which surfaces the weak-oracle problem in existing suites.
  See [`docs/ORACLE_GAP.md`](./docs/ORACLE_GAP.md).

Datasets are released under CC-BY-4.0 with a per-instance SPDX license tag, and bundles
are built to be mirrored to a DOI-bearing archive for artifact evaluation.

## Architecture

A monorepo with a hard boundary between the **model** that proposes and the **judge** that
admits. The model is fallible. The judge is deterministic.

```
engine/                         Python, the Evidence Engine
  exhibit_a/
    models/case.py              the Case data model (shared contract, mirrored in TS)
    hypothesis/generator.py     the model seam where Codex/GPT-5.6 plugs in
    hypothesis/intent.py        separate, fallible intent judge (never gates evidence)
    executor/                   swappable sandbox: docker_exec (real), local_exec (dev)
    verdict/flip_check.py       PURE, DETERMINISTIC admissibility gates, the sole judge
    verdict/...                 mutation scoring, minimization, evidence strength (scores, not gates)
    engine.py                   orchestrator
    cli.py                      the exhibit-a CLI
web/                            Next.js 15, React 19, Tailwind, the "case file" UI
  src/app/api/investigate/...   drives the engine, streams each run over SSE
fixtures/                       tiny buggy/fixed repo pairs for offline runs
```

**Security posture:** untrusted repos and PR text are assumed hostile.

- Executors run against a **disposable copy** of the checkout, so source is never mutated.
- Docker runs are network-off, capability-dropped, `no-new-privileges`, read-only rootfs.
- All untrusted input (repo URL, SHAs, claim text, model-generated patches) reaches
  `git` and shells as **argv only**, never string-interpolated, never `shell=True`.
- Remote intake is **HTTPS-only**, SHAs are hex-validated, and git hooks are disabled.
- Candidate run-commands are gated to a single scoped pytest file before execution.

## Setup

**Requirements:** Python 3.11+, Node 18+. Docker is optional for isolated runs.

### Engine

```bash
cd engine
pip install -e ".[dev]"          # or: pip install pytest ruff
python3 -m pytest -q             # 132 tests, proves the flip check and verdicts end to end
```

### Web UI

```bash
cd web
npm install
npm run dev                      # http://localhost:3000
```

## Usage

```bash
cd engine

# 1) Local buggy/fixed checkouts produce a full PROVEN flip
python3 -m exhibit_a.cli repro ../fixtures/buggy_inventory \
  --fixed ../fixtures/fixed_inventory \
  --claim "stock_for should return zero for an unknown SKU instead of raising KeyError" \
  --expect KeyError --json

# 2) A real repository at two commits (base is buggy, fix is the fixing commit or PR head)
python3 -m exhibit_a.cli repro https://github.com/org/repo.git \
  --base-sha <buggy-sha> --fix-sha <fix-sha> \
  --claim "describe the regression" --json

# 3) Deterministic replay of a sealed, known-good Case (no model, no execution)
python3 -m exhibit_a.cli repro --replay ../fixtures/cases/inventory_proven.json --json

# 4) Offline pipeline smoke test (deterministic stub instead of the model)
python3 -m exhibit_a.cli repro ../fixtures/buggy_slice \
  --fixed ../fixtures/fixed_slice --claim "..." --offline
```

The web API route `/api/investigate` drives the same engine and **streams each execution
over SSE**, so the UI shows the agent try, fail, and retry before the terminal Case. The
interface supports local and two-SHA git intake, the Prosecutor evidence gate, and a
private Silence Ledger.

Beyond `repro`, the CLI exposes the research surface as opt-in subcommands. These include
`bundle` and `verify` for signed, replayable evidence bundles, `study` for reproducibility,
`self-audit` for the false-conviction rate on innocent refactors, and `oracle-gap` for
benchmark oracle strength. See the [documentation site](https://suryanshss1011.github.io/Exhibit-A/)
for each.

## How Codex and GPT-5.6 were used

Codex with **GPT-5.6 Sol** is both the thing this was built with and a first-class
component of the product.

- **As a product component, the hypothesis generator.** Inside the engine, Codex runs in a
  **read-only sandbox** and does exactly one job. It localizes, plans, drafts a passing
  test, inverts it to fail-on-bug (pass-then-invert), and refines on execution feedback. It
  **proposes** reproductions. It **never decides a verdict.** The deterministic flip check
  alone admits a Case as `PROVEN`, from execution logs, so the product's honesty guarantee
  holds regardless of how the model behaves. This model-versus-judge split is the core
  design.
- **As the implementation partner.** Codex was the pair-programmer for the engine, the
  security boundaries, the test suite, and the streaming UI, with every change gated behind
  the same tests and lint the CI runs.

The division of labor mirrors the product's own thesis. The model reasons, but only
execution is allowed to speak.

## Status

This is a working, verified system. There are **132 engine tests** plus a typed web test
suite, all green in CI, which runs engine lint, format, and tests alongside the web build.
The deterministic verdict core, Docker sandboxing, two-SHA git intake, git-bisect culprit
attribution, mutation scoring, evidence minimization, and a full research-instrumentation
layer are implemented and tested.

## Documentation

Deep-dives live in [`docs/`](./docs/), also published as a
[docs site](https://suryanshss1011.github.io/Exhibit-A/).

- [Operations](./docs/OPERATIONS.md)
- [Executable Evidence Format](./docs/EEF.md)
- [Evidence strength](./docs/EVIDENCE_STRENGTH.md)
- [Mutation testing](./docs/MUTATION_TESTING.md)
- [Minimization](./docs/MINIMIZATION.md)
- [Self-audit](./docs/SELF_AUDIT.md)
- [Oracle-gap probe](./docs/ORACLE_GAP.md)
- [Reproducibility study](./docs/REPRODUCIBILITY_STUDY.md)
- [Research assets](./docs/RESEARCH_ASSETS.md)
- [Bug identity](./docs/BUG_IDENTITY.md)
- [Archaeology](./docs/ARCHAEOLOGY.md)
- [Triangulation](./docs/TRIANGULATION.md)
- [Property escalation](./docs/PROPERTY_ESCALATION.md)
- [Environment dataset](./docs/ENVIRONMENT_DATASET.md)

[`AGENTS.md`](./AGENTS.md) is the contract for the Codex-driven generator.

## License and citation

The toolkit is MIT ([`LICENSE`](./LICENSE)). Cite it via [`CITATION.cff`](./CITATION.cff).
Minted datasets use CC-BY-4.0 with a per-instance SPDX license tag, as described in
[Open science](#open-science) above.
