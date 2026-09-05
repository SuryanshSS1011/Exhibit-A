---
layout: default
title: Real-fix coverage pilot v6 results
---

# Real-fix coverage pilot v6 results

## Judge reach comes first

Exhibit A reached its deterministic judge on **2/30 fixes (6.7%)**. Both judged
instances reached **VERIFIED**, so the conditional result is **2/2 (100%)**, with
**0 PARTIAL**. Two judged observations are not enough to establish judge quality: the
two-sided 95% Wilson interval for VERIFIED among judged instances is **34.2%–100%**.

The whole pipeline also produced **2/30 VERIFIED (6.7%)**, **0/30 PARTIAL**, with a
two-sided 95% Wilson interval of **1.8%–21.3%**. Twenty-eight instances stopped before
the judge could rule. The honest product read therefore remains: **the current
implementation is a research instrument, not a broad-coverage product**. This pilot is
still primarily a measurement of plumbing.

## What was counted

V6 was preregistered before selection and selected under the corrected marker-aware
`uv.lock` implementation. Its immutable corpus SHA-256 is
`64ca53193d5138200b98d21363cfd178e14fb49c8b87381d32a86e5026035aa8`.
It contains 30 fixes across 21 repositories, dated February 17–August 16, 2026. No
repository contributes more than two instances. Claims are verbatim PR titles; each
buggy revision is the fixing commit's first parent.

The first outcome-free selection returned the same 30 PRs as v5. Because the request
required a fresh corpus, that selection was preserved and a pre-execution amendment
mechanically excluded all v5 PR URLs before applying the repository cap. No provider
call, build, test, Case, or verdict existed when the amendment was committed. The final
v6 corpus and v5 corpus are disjoint.

Selection losses are part of the result:

| Selection boundary | Count | Meaning |
|---|---:|---|
| Star-ranked Python repositories scanned | 192 | Held-constant public source frame |
| Repositories rejected by pinned-environment eligibility | 142 | No accepted reproducible root environment |
| Environment-eligible repositories | 50 | 38 had fresh matching fixes; 12 had none after v5 removal |
| Included instances | 30 | 21 repositories, maximum two selected per repository |
| Candidate-level eligibility exclusions | 31 | 18 had no production-Python change; 13 lacked an accepted environment on a revision |
| Prior v5 corpus members excluded | 30 | Deliberate disjointness, reported separately from eligibility |
| Eligible candidates outside the fixed sample | 978 | 868 beyond the repository cap; 110 after sample size was reached |

The selection funnel screened 61 candidates for eligibility: 30 included and 31 rejected.
That gives a descriptive end-to-end VERIFIED yield of **2/61 (3.3%)**. The 30 deliberate
v5 exclusions are not eligibility failures and are not added to that denominator. This
bookkeeping distinction was corrected after completion but before public export; it did
not alter an instance, category, or verdict.

Of the 30 selected instances, 27 use `uv.lock` on both revisions and three use
`poetry.lock`. No selected instance changes environment source between revisions.

## Ranked outcome taxonomy

| Rank | Outcome | Instances | Repositories | Share of failures | Share of all 30 |
|---:|---|---:|---:|---:|---:|
| 1 | Environment dependency install failed | 17 | 12 | 60.7% | 56.7% |
| 2 | Existing suite already failed | 4 | 2 | 14.3% | 13.3% |
| 2 | Existing suite infrastructure failure | 4 | 3 | 14.3% | 13.3% |
| 4 | Per-instance timeout | 2 | 2 | 7.1% | 6.7% |
| 5 | Checkout failed | 1 | 1 | 3.6% | 3.3% |
| — | VERIFIED | 2 | 1 | — | 6.7% |
| — | PARTIAL | 0 | 0 | — | 0.0% |

The completed public report has `provider_unavailable: 0` and `unclassified: 0`. After 19
completed rows, a quota response halted before row 20 and was not checkpointed as an
outcome. The identical command later resumed that still-unattempted row. The quota event
remains in the private audit trail; it is not model silence or an instance failure.

No selected instance landed in a candidate-rejection, no-candidate, missing-service,
provider-generation, or catch-all category. Zero-count registered categories remain in
the machine-readable report.

## Why dependency installation failed

**Recorded execution platform: Docker `linux/arm64`; host `darwin/arm64`.** These fields
are persisted in the run state so regenerating a report cannot silently change the
platform provenance.

Raw package-manager reasons remain private because third-party diagnostics can contain
local paths or other sensitive content. The public versioned classifier reports:

| Rank | Dependency-install category | Count | Share of 17 |
|---:|---|---:|---:|
| 1 | Native distribution build failure | 5 | 29.4% |
| 1 | Build backend or package metadata failure | 5 | 29.4% |
| 3 | Pinned distribution unavailable | 3 | 17.6% |
| 4 | Dependency resolution conflict | 2 | 11.8% |
| 5 | Artifact hash or integrity failure | 1 | 5.9% |
| 5 | Other install failure | 1 | 5.9% |
| — | Package index or network failure | 0 | 0.0% |
| — | Python-version incompatibility | 0 | 0.0% |

The catch-all is 5.9%, below the preregistered 20% refinement trigger, so the taxonomy
was not changed after seeing v6 outcomes.

### Did the marker-sensitive failures fall?

Yes, descriptively:

| Category | V5 | V6 | Change |
|---|---:|---:|---:|
| Pinned distribution unavailable | 7/30 | 3/30 | Fell by 4 |
| Dependency resolution conflict | 4/30 | 2/30 | Fell by 2 |
| Combined marker-sensitive failures | 11/30 | 5/30 | Fell by 6 |
| All dependency-install failures | 24/30 | 17/30 | Fell by 7 |

Both named categories fell under the preregistered same-30 denominator rule. That is
consistent with the marker fix addressing part of v5's failure mode, but it is **not a
causal estimate**: v5 and v6 use different, deliberately disjoint corpora. V6 records
`linux/arm64`; v5 was also run on arm64 but its report predates machine-readable platform
provenance. A paired rerun would be needed to attribute the difference to the code change.

## The two proofs that succeeded

Both successful instances came from `virattt/ai-hedge-fund` and were retained by the
mechanical ordering rather than chosen for tractability.

[`virattt/ai-hedge-fund#531`](https://github.com/virattt/ai-hedge-fund/pull/531), “fix:
raise ValueError for unsupported providers and remove duplicate import,” produced
`tests/test_unsupported_model_providers.py`. The exact candidate failed on the buggy state
five times with `DID NOT RAISE ValueError`, then passed on the fixed revision.

[`virattt/ai-hedge-fund#530`](https://github.com/virattt/ai-hedge-fund/pull/530), “fix:
replace bare except clauses with Exception in api.py,” produced
`tests/test_api_exception_handlers.py`. The exact candidate found the five bare handler
locations on every buggy-state rerun and passed after the fix.

## Time, model, cost, and provenance

- Run: September 4, 2026, 21:08:30–23:48:37 UTC.
- Calendar elapsed time: 9,607.5 seconds (2h 40m 7.5s), including the honest quota pause.
- Sum of measured instance-worker time: 3,235.1 seconds (53m 55.1s).
- Registered ceilings: 720 seconds per instance, 21,600 active seconds total, and 120
  seconds per test execution.
- Engine: version `0.1.0`.
- All 30 instance outcomes executed at source commit
  `7409a62708ee965a1abbe8ffb4f340e5bfb2e4ea`.
- Provider: `openai-codex-cli`; requested model `gpt-5.6-sol`.
- Confirmed model and version: explicit `unknown_no_telemetry`.
- Successful normalized proposal calls: 2.
- Actual model spend: **unavailable**, because neither call reported complete tokens or
  billable cost. It is neither `$0` nor an estimate.

The requested model's documented knowledge cutoff was February 16, 2026, and every
included fix is later. The requested identity and cutoff come from the
[official GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
but requested identity is not presented as runtime confirmation.

Post-run reporting corrections happened before public export. They separate prior-corpus
exclusions from eligibility, persist runtime platform across report regeneration, and bind
public completion time to the last immutable worker checkpoint. They changed no corpus
member, execution result, Case, failure category, dependency category, or verdict. The
[amendment record](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v6/post-run-reporting-amendment.md)
states exactly what changed and when.

## Constraints to attack first

1. **Make package builds reproducible on the declared sandbox platform.** Native builds
   and build-backend/metadata failures account for 10/17 dependency failures and one-third
   of the full corpus. A versioned base-image capability profile, safe build trace, and
   tightly allowlisted system prerequisites are the highest-leverage next experiment.
2. **Make suite preflight distinguish repo setup from a genuinely bad baseline.** Eight of
   30 instances stopped because the existing suite failed or its invocation hit
   infrastructure. Declared, reviewable suite recipes can identify the right test slice
   and prerequisites without becoming outcome-specific exceptions.
3. **Finish the remaining lock portability and bounded-execution work.** Marker-sensitive
   install categories fell but still account for 5/17 install failures; two instances
   timed out and one checkout failed. Safe resolution traces and phase-level timeout
   reporting would show whether the next lift belongs in lock export, platform artifacts,
   clone reliability, or execution budgets.

Provider prompting is not yet the measured bottleneck. Only two instances reached the
proposal-and-judge stage, and both verified. Optimizing the model before improving
environment and suite reach would optimize the least-observed stage.

## Limits on interpretation

Thirty observations clustered across 21 repositories provide a pilot signal, not a
population constant. The title/label rule is mechanical but can admit maintenance or
resource fixes; those stay in the denominator because the rule was frozen. The 100%
conditional judge result is based on two cases from one repository and must not be read as
a general judge success rate.

V5 and v6 use disjoint corpora, so their category changes are not a controlled paired
comparison. The same public repository frame, date window, target size, cap, model, and
budgets reduce some drift, but fresh instance composition remains a major confounder.

The log-free per-instance record is
[`public-report.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v6/public-report.json)
(SHA-256 `1fb026eb43a5c1c9fc094c1e0f397a2c44ffa46b0126cbf0c65609eed222156e`).
It binds corpus SHA-256
`64ca53193d5138200b98d21363cfd178e14fb49c8b87381d32a86e5026035aa8`
and private report SHA-256
`f756a78ec480217217651be97aebfde54006111cd1b77455e5bd088fee6f7ed9`.
Raw Cases, generated tests, provider diagnostics, dependency error text, and execution logs
remain private.
