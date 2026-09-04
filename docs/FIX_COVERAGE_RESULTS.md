---
layout: default
title: Real-fix coverage pilot v5 results
---

# Real-fix coverage pilot v5 results

## Judge reach comes first

Exhibit A reached its deterministic judge on **2/30 fixes (6.7%)**. On those two judged
instances it reached **2/2 VERIFIED (100%)** and **0 PARTIAL**, but two observations are far
too few to establish judge quality: the two-sided 95% Wilson interval is **34.2%–100%**.

The whole-pipeline result is **2/30 VERIFIED (6.7%)**, **0/30 PARTIAL**, with a two-sided
95% Wilson interval of **1.8%–21.3%**. The honest product read remains: **the current
implementation is a research instrument, not a broad-coverage product**. This pilot is
primarily a measurement of plumbing. Twenty-eight instances stopped before the judge
could rule.

## What was counted

The v5 corpus was selected fresh under the expanded `uv.lock` rule and frozen before any
v5 outcome in corpus SHA-256
`b95789fa86a7c26268d8b237e9fe6656a77e661525536abecc332fcbbe653f58`.
It contains 30 fixes across 25 repositories, dated February 17–July 20, 2026. No repository
contributes more than two instances. Claims are verbatim PR titles; each buggy revision is
the fixing commit's first parent.

Selection losses are part of the result:

| Selection boundary | Count | Meaning |
|---|---:|---|
| Star-ranked Python repositories scanned | 192 | Fixed v4 public source frame, rerun with v5 eligibility code |
| Repositories rejected by pinned-environment eligibility | 142 | No accepted reproducible root environment |
| Environment-eligible repositories | 50 | 40 had matching fixes; 10 had no matching bug PR |
| Candidate revisions screened to fill the run | 51 | 30 included; 21 eligibility exclusions |
| No production-Python change | 12 | Recorded exclusion, not silently dropped |
| Unsupported or invalid environment at the selected revision | 9 | Recorded exclusion, not silently dropped |
| Eligible candidates outside the fixed sample | 1,018 | 892 beyond the repository cap; 126 after sample size was reached |

Including the 21 candidate-level eligibility exclusions gives an observed end-to-end yield
of **2/51 (3.9%)**. This is descriptive, not an alternate headline.

## What `uv.lock` changed

Pilot v4 needed to scan 490 repositories to find 50 accepted environments. V5 found 50
after 192: the retained fraction rose from **10.2% to 26.0%**, a **2.55×** increase. Of the
50 v5 environments, 33 were accepted through `uv.lock`; 22 of the 30 selected instances
used the `uv.lock` path. The other selected environments were four Poetry and four pinned
requirements environments.

That selection gain did not translate into broad execution reach. V4 had 17/30 dependency
image failures; v5 had 24/30 dependency-install failures. This is not evidence that
`uv.lock` support made execution worse—the corpora differ. It shows that recognizing a
lockfile and reproducing its resolved environment are separate constraints.

## Ranked outcome taxonomy

| Rank | Outcome | Instances | Repositories | Share of failures | Share of all 30 |
|---:|---|---:|---:|---:|---:|
| 1 | Environment dependency install failed | 24 | 20 | 85.7% | 80.0% |
| 2 | Existing suite infrastructure failure | 2 | 2 | 7.1% | 6.7% |
| 3 | Existing suite already failed | 1 | 1 | 3.6% | 3.3% |
| 4 | Per-instance timeout | 1 | 1 | 3.6% | 3.3% |
| — | VERIFIED | 2 | 2 | — | 6.7% |
| — | PARTIAL | 0 | 0 | — | 0.0% |

The completed outcome report has `provider_unavailable: 0`. A quota response halted the
run at 26 completed instances and left instance 27 unattempted, exactly as preregistered.
After capacity returned, the identical command resumed the remaining four. Quota was
therefore neither a failed Case nor model silence.

No selected instance landed in a candidate-rejection, no-candidate, checkout, missing
service, provider-generation, or catch-all category. Zero-count registered categories are
retained in the machine-readable report.

## Why dependency installation failed

Raw package-manager text remains private because it can contain third-party paths or
diagnostics. The versioned classifier publishes these categories:

| Rank | Dependency-install category | Count | Share of 24 |
|---:|---|---:|---:|
| 1 | Pinned distribution unavailable | 7 | 29.2% |
| 2 | Dependency resolution conflict | 4 | 16.7% |
| 2 | Native distribution build failure | 4 | 16.7% |
| 2 | Build backend or package metadata failure | 4 | 16.7% |
| 5 | Artifact hash or integrity failure | 3 | 12.5% |
| 6 | Other install failure | 2 | 8.3% |
| — | Package index or network failure | 0 | 0.0% |
| — | Python-version incompatibility | 0 | 0.0% |

The catch-all is 8.3%, below the preregistered 20% refinement trigger, so the taxonomy was
not changed after seeing v5 outcomes.

## The two proofs that succeeded

[`virattt/ai-hedge-fund#502`](https://github.com/virattt/ai-hedge-fund/pull/502), “fix: use
target downside deviation in Sortino ratio,” produced
`tests/backtesting/test_sortino_target_downside_deviation.py`. The same observed signature
appeared on all five buggy-state reruns, and the exact test passed on the fixed revision.
This is the proof already seen in v4, now admitted independently in the fresh v5 run.

[`ahujasid/blender-mcp#266`](https://github.com/ahujasid/blender-mcp/pull/266), “fix: render
viewport screenshots offscreen so they aren't black when the window isn't foreground,”
produced `test_viewport_screenshot_offscreen.py`. The first proposal was rejected because
it prohibited the intentional screenshot fallback. Refinement preserved the fallback and
tested the required offscreen render path; that exact test failed five times on the buggy
revision and passed on the fix.

## Time, model, cost, and provenance

- Run: September 4, 2026, 15:25:45–18:28:08 UTC.
- Calendar elapsed time from the initial start to final checkpoint: 10,943 seconds
  (3h 2m 23s), including the honest quota halt and runtime-amendment validation delay.
- Sum of measured instance-worker time: 2,098.9 seconds (34m 58.9s).
- Registered ceilings: 720 seconds per instance, 21,600 active seconds total, and 120
  seconds per test execution.
- Engine: version `0.1.0`.
- Instances 1–26 executed at source commit
  `445f4982a3d93ec6e10e96adf4e038bcc98798ab`.
- Instances 27–30 executed at source commit
  `e1b4a0799c612224a55833789c0ca7329f0c1c99`, after the recorded resume-only amendment.
  The amendment changed no selection, run parameter, Case, category, or verdict code.
- Provider: `openai-codex-cli`; requested model `gpt-5.6-sol`.
- Confirmed model and version: explicit `unknown_no_telemetry`.
- Successful normalized proposal calls: 4.
- Actual model spend: **unavailable**, because no call reported complete tokens or billable
  cost. It is not reported as `$0` and is not estimated from incomplete telemetry.

The requested model's documented knowledge cutoff was February 16, 2026, so all included
fixes are later. The model identity, cutoff, and pricing snapshot come from the
[official GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
but the requested identity is not presented as runtime confirmation.

The quota halt also exposed a resumability defect: the parent correctly declined to
checkpoint the quota response, while the worker cache still treated it as reusable. The
failure was recorded after 26 outcomes and before row 27. Commit `e1b4a07` retains the
quota attempt as a private audit record while allowing a new worker attempt after capacity
returns. The public report records both execution segments instead of attributing the
whole run to one revision.

## Constraints to attack first

1. **Make resolved dependencies portable across the fixed container platform.** Unavailable
   distributions, resolution conflicts, and hash/integrity failures account for 14/24
   install failures. The next instrument should preserve a safe package-level resolution
   trace and distinguish missing platform artifacts from lock-export defects.
2. **Support native and package-build prerequisites without weakening isolation.** Native
   compilation and build-backend/metadata failures account for another 8/24. A versioned
   base-image capability profile and allowlisted system packages would test how much of
   this block can be lifted reproducibly.
3. **Add repository-specific suite recipes only as declared, reviewable inputs.** Two runs
   reached an infrastructure failure, one started from a failing existing suite, and one
   timed out. Recipes can name the correct suite slice and required sandbox services, but
   must not become outcome-driven exceptions.

Provider proposal quality is not the next measured bottleneck: only two instances reached
the judge, and both verified. Improving prompts before improving environment reach would
optimize the least-observed stage.

## Limits on interpretation

Thirty clustered observations provide a signal, not a population constant. The title-prefix
rule is mechanical but admits some maintenance or resource fixes; they remain in the
denominator because the rule was frozen. V4 and v5 use different corpora, so their outcome
counts are not a controlled paired comparison. The v4 public repository frame was held
constant to isolate eligibility reach, but only six PRs overlap.

The log-free per-instance record is
[`public-report.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v5/public-report.json)
(SHA-256 `2cebce394f47ade35dfdd71c6026ac4f04507e63a944b770d67dbfa1b6841ed2`).
It binds corpus SHA-256
`b95789fa86a7c26268d8b237e9fe6656a77e661525536abecc332fcbbe653f58`
and private report SHA-256
`90fc9f161dc8711294c1169c622738ddb4baedafedfd678d437098416b3ab2d0`.
Raw Cases, generated tests, provider diagnostics, dependency error text, and execution logs
remain private.
