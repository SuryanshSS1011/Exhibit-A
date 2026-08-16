---
layout: default
title: Behavior-preserving refactor claims
---

# Behavior-preserving refactor claims

Behavior-preserving refactors are Exhibit A's second claim type. The first deterministic
unit compares a trusted behavioral contract executed repeatedly before and after a
refactor. It does not call a model and does not reuse or weaken the bug-reproduction
`flip_check`.

The judge compares contract outcomes rather than pytest's raw stdout, which contains
paths, timing, and other non-behavioral noise. Application outputs belong in explicit
contract assertions. Its default is three runs per state, and callers cannot lower the
requirement below two because one execution cannot establish determinism.

| Observation | Verdict | Execution truth | Goal truth |
|---|---|---|---|
| Contract passes deterministically in both states | `VERIFIED` | `COMPLETED` | `VERIFIED` |
| Stable outcome or failure signature changes between states | `FAILED` | `COMPLETED` | `FAILED` |
| The same complete, parseable failure fingerprint occurs in both states | `PARTIAL` | `COMPLETED` | `PARTIAL` |
| Missing reruns, flakiness, or inconsistent failures | `UNCERTAIN` | `NOT_RUN` or `COMPLETED` | `UNCERTAIN` |
| Timeout, import, collection, syntax, or harness failure | `UNCERTAIN` | `FAILED` | `UNCERTAIN` |

`FAILED` therefore has a narrow, evidence-backed meaning: the preservation goal was
deterministically disproved. A broken environment is never `FAILED`; it is `UNCERTAIN`.
Opaque failures that cannot be fingerprinted are also `UNCERTAIN`, never assumed equal.
For the initial pytest contract runner, only exit code 1 is a behavioral test failure;
internal, usage, collection, timeout, signal, and no-tests outcomes are infrastructure.
Release truth remains `NOT_ASSESSED` for every result because passing a selected contract
does not establish that a refactor is safe to ship.

The fixed-shape runner injects only `test_refactor_contract.py`, invokes only that test,
disables network requests in its execution specification, and prepares each code state
once before the repeated runs. `DockerExecutor` supplies the production containment
boundary; `LocalExecutor` is intentionally limited to trusted fixtures and development
because it inherits host network and credentials. The counterexample fixture is excluded
from the innocent-pair self-audit manifest and exists only to prove that observable changes
produce `FAILED`.

`behavior-refactor-evidence/v2` is the machine-readable result shape. It links every raw
base and target run to one validated connector evidence ID, includes the deterministic
truth assessment, binds every receipt to the same contract artifact, and retains the
bounded local image handle needed to recompute each request digest. A single invalid
receipt aborts collection before a verdict is returned.

EEF v2 can archive this evidence with both source states. Offline integrity verification
checks the signature, tree digests, exact run-to-receipt linkage, and independently
re-derives the recorded truth. Executable verification then rebuilds and repeats both
archived states and compares the complete fresh result with the recorded one. A stable
`FAILED` result can therefore be successfully replay-verified: replay truth means “the
record is reproducible,” not “the refactor passed.” `exhibit-a passport` can now project
this machine evidence into a credential-free public JSON passport; source snapshots,
raw execution logs, and opaque executor metadata remain only in the private EEF because
they may contain sensitive data.

The CLI collector uses the same resource-bounded, network-disabled Docker harness as EEF
replay rather than the host-local development runner:

```bash
python3 -m exhibit_a.cli refactor-bundle \
  --base-source /path/to/before --target-source /path/to/after \
  --contract /path/to/test_contract.py \
  --signing-key /secure/eef.key --out refactor.eef
```

The command rejects identical source paths, keys or outputs inside a source tree, and any
key aliased to the contract or output. It then materializes each checkout once through
EEF's no-symlink, race-safe source reader and uses those exact snapshots for every
execution and for the archive. Collection and replay share the byte-exact Dockerfile,
fixed pytest argv, offline build, 64 KiB per-stream output cap, and CPU/memory/PID/time
limits. Contracts are capped at 1 MiB so the complete repeated evidence remains one valid
EEF entry. The contract file is injected at the fixed `test_refactor_contract.py` path in
both states; it is not copied from either checkout. The command closes the executor on
success or failure.
