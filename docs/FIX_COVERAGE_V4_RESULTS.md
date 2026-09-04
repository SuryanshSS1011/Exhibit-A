---
layout: default
title: Real-fix coverage pilot v4 results
---

# Real-fix coverage pilot v4 results

## Headline

On the frozen 30-instance pilot, Exhibit A reached **VERIFIED on 1/30 fixes (3.3%)**.
It reached **PARTIAL on 0/30**; PARTIAL is not merged into the headline. The two-sided 95%
Wilson interval for VERIFIED is **0.6%–16.7%**.

The honest product read is: **the current implementation is a research instrument, not a
broad-coverage product**. The one admitted result proves the full pipeline can work on a
post-cutoff real fix. The remaining 29 show that its practical envelope is presently much
narrower than “Python repositories that build in a sandbox” sounds.

## What was counted

The denominator was frozen before execution in corpus SHA-256
`b293d97d3a3f786339bc964460562b0824aec960323c6f8fe84406bddae26cd4`.
It contains 30 fixes across 15 repositories, dated February 17–August 21, 2026. No
repository contributes more than three instances. Claims are verbatim PR titles; the
buggy revision is the fixing commit's first parent.

The selection history is part of the finding, not cleanup:

- Pilot v1's top-50 frame yielded no instances: 45 repositories failed pinned-environment
  eligibility, four had no matching `bug` PR, and one clone failed.
- Pilot v2 scanned 490 repositories to find the first 50 accepted environments, but exact
  PR-level `bug` labels yielded only five instances from one repository.
- Pilot v3 preregistered the title-prefix fix signal, then retained only 16 instances when
  50 GitHub metadata reads failed during a transport outage.
- Pilot v4 kept the v3 frame unchanged, added bounded metadata retries, and froze 30
  instances before the first model call.

Across v4 selection, 440/490 star-ranked Python repositories (89.8%) did not pass the
current root pinned-environment parser. Among candidate PRs, 20 more failed revision-level
production-Python or lock checks before the 30-item run. If those 20 eligibility exclusions
are included, the observed end-to-end yield is 1/50 (2.0%). The other 391 recorded
candidate exclusions are sampling boundaries—360 beyond the per-repository cap and 31
after the target size—not failed Exhibit A runs.

## Ranked failure taxonomy

| Rank | Outcome | Instances | Repositories | Share of all 30 |
|---:|---|---:|---:|---:|
| 1 | Dependency image could not be built | 17 | 9 | 56.7% |
| 2 | Provider quota exhausted before a candidate | 6 | 3 | 20.0% |
| 3 | Existing suite could not be evaluated safely | 4 | 3 | 13.3% |
| 4 | Checkout failed | 1 | 1 | 3.3% |
| 5 | Existing suite already failed | 1 | 1 | 3.3% |
| — | VERIFIED | 1 | 1 | 3.3% |

The preregistered taxonomy called the six quota failures `no_candidate_proposed`. After all
outcomes were fixed, their recorded silence reasons showed explicit Codex usage-limit
errors. The public report therefore refines them to `provider_quota_exhausted` while
retaining the original taxonomy. This changed no Case or verdict. No failure landed in a
catch-all category.

All other registered categories had count zero: no candidate was rejected for a wrong
failure signature, infrastructure, vacuity, tamper, flakiness, policy, failure to fail on
the buggy state, or failure to pass on the fixed state; no run timed out; and no selected
revision lacked a supported lockfile. These zeroes are retained in the machine-readable
taxonomy rather than omitted.

## The proof that succeeded

[`virattt/ai-hedge-fund#502`](https://github.com/virattt/ai-hedge-fund/pull/502), “fix: use
target downside deviation in Sortino ratio,” produced a generated test at
`tests/backtesting/test_sortino_target_downside.py`. The same assertion signature appeared
in five buggy-state failures, with run times from 6.20 to 6.84 seconds, and the test passed
on the fixed commit in 6.55 seconds. The two successful provider operations took about
234 and 126 seconds. Post-verdict minimization and strength scoring were disabled exactly
as preregistered because they cannot change admission and would add pilot cost.

## Time, model, and cost

- Run: September 4, 2026, 06:12:59–06:39:19 UTC.
- End-to-end wall clock: 1,580.0 seconds (26m 20s).
- Sum of instance worker time: 1,571.9 seconds (26m 12s).
- Registered ceilings: 720 seconds per instance, 21,600 active seconds total, and 120
  seconds per test execution.
- Engine: version `0.1.0`, source commit
  `d37312c82b4f38f49254787deb0a0a316ed83c64`.
- Provider: `openai-codex-cli`; requested model `gpt-5.6-sol`.
- Confirmed model and version: explicit `unknown_no_telemetry`.
- Successful normalized model calls: 2.
- Actual model spend: **unavailable**, because the CLI reported neither complete token
  usage nor billable cost. It is not reported as `$0` or estimated from incomplete data.

The requested model's documented knowledge cutoff was February 16, 2026, so all included
fixes are later. The pricing snapshot used at preregistration came from the
[official GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
but no dollar estimate is defensible without complete tokens and cache accounting.

## Constraints to attack first

1. **Environment construction.** This dominates both selection and execution. Support
   modern lock layouts and project-native installers; separate runtime from dev/test
   requirements; supply pinned system build packages and platform-aware resolution. This
   opens 17/30 runtime-blocked cases, but does not imply they would then verify.
2. **Provider capacity and telemetry.** Reserve a study budget before starting, fail the
   run preflight when quota is insufficient, and prefer a provider that reports confirmed
   identity, tokens, and spend. Six cases reached this boundary but never got a candidate;
   they must not be treated as model silence.
3. **Suite recipes and services.** Four suites exited as infrastructure failures. A
   versioned, reviewable repository recipe should name the correct suite command and any
   sandbox services without weakening network or credential isolation.

## Limits on interpretation

Thirty clustered observations give a signal, not a population constant. The title-prefix
rule is mechanical but admits some styling, resource, or maintenance fixes that may sit
outside deterministic functional behavior; they remain in the denominator because the
rule was frozen. Six quota failures also mean this run cannot estimate proposer success
conditional on a healthy provider. A future replication should preregister a fresh corpus,
reserve model capacity, and keep these v4 outcomes untouched rather than retrying them
until the number rises.

The log-free per-instance data are in the
[`public-report.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v4/public-report.json)
(SHA-256 `2472bf2319063805bf57104804453dad9fa8fe7450df45b0238d22b647ab4e62`).
Raw Cases, generated test bodies, provider diagnostics, and execution logs remain private.
