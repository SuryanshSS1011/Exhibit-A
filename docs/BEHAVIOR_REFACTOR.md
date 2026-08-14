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
