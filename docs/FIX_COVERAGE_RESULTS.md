---
layout: default
title: Real-fix coverage pilot v8 results
---

# Real-fix coverage pilot v8 results

## The generalization result

Exhibit A reached its deterministic judge on **15/30 fixes (50.0%)** and produced
**9/30 VERIFIED results (30.0%)**, with a two-sided 95% Wilson interval of
**16.7%–47.9%**. **0/30 were PARTIAL**. Among the instances the judge could inspect,
**9/15 VERIFIED (60.0%)**, with a 95% Wilson interval of **35.7%–80.2%**.

V8 is the first corpus whose repositories were excluded whole if they appeared in v5,
v6, or v7. Its 30 instances span 17 repositories that were not examined while the engine
changes under test were developed. This result—not v7's reused-repository corpus—is the
evidence that the engine's gains generalize beyond repositories they were fitted to.

The honest read is encouraging but bounded: Exhibit A now has a credible narrow-product
result, while half the corpus still stopped before the judge. It is best described as a
**narrow, silence-tolerant product backed by research instrumentation**, not a broad
Python-fix verifier.

## Probe before spend

The frozen provider-free probe reached the judge on **17/30 instances (56.7%)** with zero
model calls, clearing the preregistered gate of 3/30. The provider run reached 15/30.
Two probe-reachable instances—`moonshotai-kimi-cli-pr-1269` and
`aden-hive-hive-pr-4869`—hit the registered 720-second ceiling during the provider run.
Every dependency-install and checkout outcome otherwise agreed between phases.

| Measure | Probe | Provider run |
|---|---:|---:|
| Completed | 30/30 | 30/30 |
| Reached judge | 17/30 (56.7%) | 15/30 (50.0%) |
| VERIFIED | not measured | 9/30 (30.0%) |
| PARTIAL | not measured | 0/30 (0.0%) |
| Provider calls | 0 | 37 |

## Mechanical corpus and exclusions

The corpus SHA-256 is
`edfbd4f423901b8729eaad6210c87c2d3ad5765610a04090f1d6defc0cc838af`.
Selection screened 333 star-ranked Python repositories, removed 29 prior-corpus
repositories before eligibility, retained the first 50 fresh repositories with accepted
pinned environments, and selected 30 instances across 17 repositories.

| Selection boundary | Count |
|---|---:|
| Star-ranked repositories screened | 333 |
| Prior-corpus repositories excluded | 29 |
| Environment-ineligible repositories | 254 |
| Fresh environment-eligible repositories retained | 50 |
| Included instances | 30 |
| Candidate-level eligibility exclusions | 48 |
| Candidates outside the repository cap | 635 |
| Eligible candidates after the sample filled | 54 |

The 48 candidate-level exclusions were 32 fixes without a production-Python change and
16 without an accepted pinned environment on at least one revision. Nine proofs from 78
fully screened candidates give a descriptive selection-to-proof yield of **11.5%**.

## Ranked outcomes

| Rank | Outcome | Instances | Repositories | Share of 21 non-VERIFIED | Share of all 30 |
|---:|---|---:|---:|---:|---:|
| 1 | Environment dependency install failed | 10 | 7 | 47.6% | 33.3% |
| 2 | Candidate did not fail on buggy state | 3 | 3 | 14.3% | 10.0% |
| 2 | Candidate infrastructure failure | 3 | 3 | 14.3% | 10.0% |
| 2 | Checkout failed | 3 | 2 | 14.3% | 10.0% |
| 5 | Timed out | 2 | 2 | 9.5% | 6.7% |
| — | VERIFIED | 9 | 7 | — | 30.0% |
| — | PARTIAL | 0 | 0 | — | 0.0% |

The complete report records `provider_unavailable: 0`, `unclassified: 0`, and no final
halt. Three quota responses paused collection without checkpointing the next instance;
each resume skipped every completed checkpoint.

## Environment failures and host scope

The run used Linux/arm64 Docker sandboxes on a Darwin/arm64 host. Docker exposed
**3.83 GiB**, so memory-sensitive failures remain scoped to this machine.

| Dependency-install category | Count | Share of 10 |
|---|---:|---:|
| Pinned distribution unavailable | 5 | 50.0% |
| Other install failure | 3 | 30.0% |
| Package build backend or metadata failure | 2 | 20.0% |
| All other registered categories | 0 | 0.0% |

The 30% catch-all exceeds the classifier's warning threshold and is too coarse to rank the
next environment constraint precisely. The preregistered no-fix rule was honored: the
engine, selection, and taxonomy were not changed after outcomes appeared.

## What the nine proofs covered

The verified cases cover removal of deprecated importable modules, exact-match fuzzy
search, safe handling of an uninitialized device state, missing CLI namespace attributes,
virtual-environment interpreter selection, CLI authentication fallback, accessibility
recovery ordering, stale UI text, and guarding a song-information action when no song is
playing. The frozen order produced nine proofs across seven repositories; none was added
after tractability was observed.

Every VERIFIED Case records `truth.execution: COMPLETED`, `silence_reason: null`, five
matching buggy-state failures, and a fixed-state pass. Minimization was disabled exactly
as preregistered. The self-contained
[`provider-result.md`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v8/provider-result.md)
links every source PR and describes each proof.

## Time, model, and reproducibility

- Provider active time: 6,580.2 seconds (1h 49m 40s).
- Provider calendar interval: 62h 10m 14s, including quota waits.
- Probe active time: 1,970.2 seconds (32m 50s).
- Platform: Docker `linux/arm64`; host `darwin/arm64`.
- Provider: `openai-codex-cli`; requested model `gpt-5.6-sol`.
- Confirmed model/version: explicit `unknown_no_telemetry`.
- Provider calls: 37.
- Model spend and token totals: unavailable, not zero and not estimated.
- Probe revision: `e4a6be0d61400bf013e7f589cc5986faaa2cc61f`.
- Provider revision: `5fc98e04087dca6c7911001a9e46401baef9900d`; the intervening
  commit froze probe artifacts only, with no engine change.

The log-free
[`public-report.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v8/public-report.json)
has SHA-256 `d5bca6b3b61b26bbdd3785b4d05f44846ee93db6d2fefba7864fb44251a7948a`.
It binds the preregistration, corpus, and private report by hash. Raw Cases, generated
tests, provider diagnostics, dependency text, and execution logs remain private.

## What to attack next

1. **Environment portability.** Ten dependency installs and three checkouts stopped before
   the judge. The five unavailable pinned distributions are the largest precise category;
   the three catch-all installs need a preregistered taxonomy refinement before choosing
   an engineering response.
2. **Bounded provider latency.** Two environments that passed the free probe consumed the
   full 720-second provider budget without reaching the judge.
3. **Executable candidate quality.** Six judged candidates were rejected evenly between
   not failing on the buggy state and infrastructure failure. Better phase-specific
   feedback may recover these without weakening the deterministic gate.

Thirty instances and one architecture still leave a wide interval. A larger
repository-disjoint replication and an amd64 run are the next steps before treating 30%
as a stable coverage constant.


## Postscript: the platform is not neutral

Added after publication and deliberately separate from the result above, which is not
restated or revised.

Every pilot from v5 to v8 ran on `linux/arm64`, and each report records that. A later
provider-free probe of v8's frozen corpus on `x86_64` reached the deterministic judge on
24 of 30 instances where the same corpus reached 17 on arm64. Three of the differences
were checkout failures caused by a missing Git LFS filter rather than by anything in the
repositories, and two were distributions with no arm64 build at all: `daal`, which is
Intel's, and `cel-expr-python`.

That probe is not a coverage result and cannot be compared with the 9/30 above. It ran
with a stub proposer, made no model call, and was executed at a later revision with
engine changes the pilot did not have. What it establishes is narrower and still
important: **the published figure is a floor under its recorded platform, not a
platform-independent measurement.** A corrected figure needs a pilot preregistered on
`x86_64` to earn it, and none is claimed here.

Historical results remain available for [v7](./FIX_COVERAGE_V7_RESULTS.html),
[v6](./FIX_COVERAGE_V6_RESULTS.html), [v5](./FIX_COVERAGE_V5_RESULTS.html), and
[v4](./FIX_COVERAGE_V4_RESULTS.html).
