---
layout: default
title: Real-fix coverage pilot v7 results
---

# Real-fix coverage pilot v7 results

## Judge reach comes first

Exhibit A reached its deterministic judge on **15/30 fixes (50.0%)**. Of those judged
instances, **7/15 VERIFIED (46.7%)**, with a two-sided 95% Wilson interval of
**24.8%–69.9%**. **0/30 were PARTIAL**; PARTIAL is never merged into VERIFIED.

Across the entire mechanically selected corpus, Exhibit A produced **7/30 VERIFIED
(23.3%)**, with a two-sided 95% Wilson interval of **11.8%–40.9%**. The other 23 were
honest UNCERTAIN outcomes: 15 dependency installations failed and eight proposed
candidates were rejected by the judge.

This is a material improvement in what the pilot can observe, not grounds for calling the
system broad coverage. Half the corpus still stopped before the judge, and more than half
of judged candidates did not prove the claim. The honest read is: **Exhibit A remains a
research instrument today, but v7 contains the first credible product signal**. Seven
real, post-cutoff fixes across seven repositories were proved without tuning the corpus.

## The free probe predicted reach

Before any model call, the preregistered `--probe` run reached the judge on **15/30
(50.0%)** and failed dependency installation on the other 15. The stub candidate was
rejected as vacuous in every reachable environment, exactly as designed. Because 15
exceeded the preregistered 3/30 gate, the provider run proceeded on the identical corpus
without an engine or environment change.

The provider run also reached the judge on **15/30**. That exact agreement is useful: in
this sample, environment reach—not provider availability or proposal silence—determined
whether the judge could be exercised. The probe made zero model calls and did not measure
verification.

For descriptive context, the post-fix probe on the frozen, optimistically biased v6
corpus reached 13/30. V7 reached 15/30 on PRs absent from both v5 and v6, so the engine-side
reach gains generalized to a fresh sample. The corpora are not paired; the difference is
not a causal estimate.

## What was counted

V7 was preregistered before selection. The final corpus SHA-256 is
`f0cc23ecd53c4289da248904a9f38f90de56a9f7613f4e7c80704fe0d2d74282`.
It contains 30 fixes across 21 repositories, dated February 17–August 16, 2026. No
repository contributes more than two instances. Claims are verbatim PR titles; each buggy
revision is the fixing commit's first parent. The corpus is disjoint from both v5 and v6.

One selector-generated ID copied the period in `plotly.py`, which the runner's ID grammar
rejects. Before any clone, execution, outcome, or provider call, the ID alone was changed
from `plotly-plotly.py-pr-5517` to `plotly-plotly-py-pr-5517`. The PR, claim, revisions,
order, and all other instances were unchanged. The original hash and zero-observation
timing are preserved in the
[`amendment record`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v7/amendment-001.json).

Selection losses remain part of the result:

| Selection boundary | Count | Meaning |
|---|---:|---|
| Star-ranked Python repositories scanned | 192 | Held-constant public source frame |
| Repositories rejected by pinned-environment eligibility | 142 | No accepted reproducible root environment |
| Environment-eligible repositories | 50 | 36 had fresh matching fixes; 14 had none after prior-corpus removal |
| Included instances | 30 | 21 repositories, maximum two selected per repository |
| Candidate-level eligibility exclusions | 42 | 28 had no production-Python change; 14 lacked an accepted environment on a revision |
| Prior v5/v6 corpus members excluded | 60 | Deliberate disjointness, separate from eligibility |
| Eligible candidates outside the fixed sample | 937 | 842 beyond the repository cap; 95 after sample size was reached |

The selection funnel screened 72 candidates for instance eligibility: 30 included and 42
rejected. That gives a descriptive selection-to-proof yield of **7/72 (9.7%)**. The 60
deliberate prior-corpus exclusions are not eligibility failures and are not added to that
denominator.

Twenty-seven selected instances use `uv.lock` on both revisions, two use `poetry.lock`,
and one uses pinned `requirements*.txt`. No selected instance changes environment source
between revisions.

## Ranked outcome taxonomy

| Rank | Outcome | Instances | Repositories | Share of 23 non-VERIFIED outcomes | Share of all 30 |
|---:|---|---:|---:|---:|---:|
| 1 | Environment dependency install failed | 15 | 10 | 65.2% | 50.0% |
| 2 | Candidate infrastructure failure | 3 | 2 | 13.0% | 10.0% |
| 3 | Candidate vacuous | 2 | 1 | 8.7% | 6.7% |
| 3 | Candidate wrong failure signature | 2 | 2 | 8.7% | 6.7% |
| 5 | Candidate tamper | 1 | 1 | 4.3% | 3.3% |
| — | VERIFIED | 7 | 7 | — | 23.3% |
| — | PARTIAL | 0 | 0 | — | 0.0% |

The completed report has `provider_unavailable: 0` and `unclassified: 0`. There were no
timeouts, checkout failures, proposal-silence outcomes, provider-generation failures,
suite-preflight stops, or catch-all candidate rejections.

An explicit quota response halted after 16 completed rows and did not checkpoint the next
row as an outcome. Once capacity was available, the identical command resumed the still
unattempted row and completed all 30. The quota event remains private operational history;
it is not model silence, provider unavailability, or an instance failure.

## Why dependency installation failed

**Recorded execution platform: Docker `linux/arm64`; host `darwin/arm64`.** Raw
package-manager diagnostics remain private. The versioned public classifier reports:

| Rank | Dependency-install category | Count | Share of 15 |
|---:|---|---:|---:|
| 1 | Build backend or package metadata failure | 7 | 46.7% |
| 2 | Native distribution build failure | 5 | 33.3% |
| 3 | Artifact hash or integrity failure | 1 | 6.7% |
| 3 | Dependency resolution conflict | 1 | 6.7% |
| 3 | Pinned distribution unavailable | 1 | 6.7% |
| — | Other install failure | 0 | 0.0% |
| — | Package index or network failure | 0 | 0.0% |
| — | Python-version incompatibility | 0 | 0.0% |

No catch-all refinement was required, and no environment or classifier code was changed
after seeing the result. The two marker-sensitive categories named before v6 continued to
fall descriptively: pinned-distribution and resolution-conflict failures total 2/30 in v7,
versus 5/30 in v6 and 11/30 in v5. The pilots use disjoint corpora, so this trend is
consistent with improvement but does not isolate its cause.

## The seven proofs

Every proof came from the frozen mechanical order; none was added after a tractable result
was observed.

1. [`virattt/ai-hedge-fund#549`](https://github.com/virattt/ai-hedge-fund/pull/549)
   proved that a company-news payload without an author is accepted and defaults the
   optional field to `None`.
2. [`HKUDS/LightRAG#2723`](https://github.com/HKUDS/LightRAG/pull/2723) proved that
   PostgreSQL vector-table and supporting-index creation emits intrinsically idempotent
   `IF NOT EXISTS` SQL.
3. [`volcengine/OpenViking#205`](https://github.com/volcengine/OpenViking/pull/205) proved
   that overlong Markdown filenames are deterministically shortened within filesystem
   limits while retaining a collision-resistant suffix.
4. [`run-llama/llama_index#20733`](https://github.com/run-llama/llama_index/pull/20733)
   proved that the LayoutIR integration advertises the Python floor required by its
   dependency.
5. [`ahujasid/blender-mcp#322`](https://github.com/ahujasid/blender-mcp/pull/322) proved
   that the packaged Blender add-on is byte-for-byte synchronized with the canonical
   source.
6. [`spotDL/spotify-downloader#2628`](https://github.com/spotDL/spotify-downloader/pull/2628)
   proved that playlist metadata handles Spotify's new `item` response key equivalently
   to the legacy `track` key.
7. [`plotly/plotly.py#5517`](https://github.com/plotly/plotly.py/pull/5517) proved that
   `write_image` forwards `None` when no engine is supplied, avoiding a spurious engine
   deprecation warning.

Each VERIFIED Case records `truth.execution: COMPLETED`, `silence_reason: null`, five
matching buggy-state failures, and a fixed-state pass. Minimization was disabled exactly
as preregistered, so these are the proposed tests rather than post-verdict reductions.

## Time, model, cost, and provenance

- Provider run: September 6, 2026, 11:55:51–15:36:53 UTC.
- Provider-run calendar interval: 13,261.5 seconds (3h 41m 1.5s), including host sleep
  and the quota halt/resume.
- Sum of measured provider-run instance-worker time: 5,312.6 seconds (1h 28m 32.6s).
- Probe calendar interval: 5,733.0 seconds (1h 35m 33s); active worker time 2,418.0
  seconds (40m 18s).
- Registered ceilings: 720 seconds per instance, 21,600 active seconds total, and 120
  seconds per test execution.
- Engine: version `0.1.0`.
- Probe execution revision: `546b3b2388a0ea54dad1598087935a4614098404`.
- Provider execution revision: `f939dc926af6118a8287218d9ebe01276ef210f4`.
- Provider: `openai-codex-cli`; requested model `gpt-5.6-sol`.
- Confirmed model and version: explicit `unknown_no_telemetry`.
- Recorded normalized proposal/refinement calls: 42.
- Actual model spend and token totals: **unavailable** because the CLI reported neither
  complete usage nor billable cost. This is not `$0` and is not estimated.

The requested model's documented knowledge cutoff was February 16, 2026, and every
included fix is later. The requested identity and cutoff come from the
[official GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
but requested identity is not presented as runtime confirmation.

## Constraints to attack first

1. **Make package builds portable on the declared sandbox platform.** Build-backend and
   native-build failures account for 12/30 instances—40% of the entire corpus and 80% of
   installation failures. This is the largest single reach constraint by far. A versioned
   base-image capability profile and tightly allowlisted native prerequisites should be
   the next preregistered experiment.
2. **Make judged candidates executable without weakening isolation.** Three candidates
   reached the judge but failed on infrastructure, across two repositories. Phase-specific
   execution diagnostics and safe, declared test prerequisites could recover observation
   here without teaching the judge to accept infrastructure noise.
3. **Improve evidence validity at proposal time.** Five judged candidates were vacuous,
   matched the wrong failure signature, or attempted tamper. Better feedback for import
   reachability, target-signature selection, and test-file boundaries could move these
   cases; the deterministic rejection rules should remain unchanged.

Residual lock portability—one hash failure, one resolution conflict, and one unavailable
distribution—still matters, but it is now 3/30 rather than the dominant marker-related
failure seen in v5.

## Honest product read and limits

V7 is stronger than a judge demo: the engine reached half of a fresh corpus and proved
nearly a quarter end to end. That is enough to justify product work for a deliberately
narrow, silence-tolerant use case. It is not enough to market Exhibit A as broad Python-fix
verification. A user still receives no ruling on half of selected fixes before the model
can try, and only 7/15 judged candidates proved the claim.

Thirty observations clustered across 21 repositories are a pilot signal, not a population
constant. The mechanical title/label rule can admit packaging, synchronization, or
maintenance fixes alongside behavioral bugs; they stay in the denominator because the
rule was frozen. The environment was Linux/arm64 only. Runtime model identity and cost
were not exposed by the CLI. Later, larger preregistered corpora and another architecture
are needed before treating 23.3% as a stable coverage estimate.

The log-free per-instance record is
[`public-report.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v7/public-report.json)
(SHA-256 `e40c0283b24dc1fe89aa14ce271380f465c62e90aadea0e01cb18c0a69edd237`).
It binds preregistration SHA-256
`319b1112c0868b7377d340e5e0a863b23ad8dbce8830786436507ec893c089ae`,
corpus SHA-256
`f0cc23ecd53c4289da248904a9f38f90de56a9f7613f4e7c80704fe0d2d74282`,
and private report SHA-256
`1b27905a69f6512a96af55916ff3d09300de6d203840cfac530a40a1373ad105`.
Raw Cases, generated tests, provider diagnostics, dependency error text, and execution logs
remain private.
