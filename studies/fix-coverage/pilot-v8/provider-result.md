# Pilot v8 provider result

## Headline

Exhibit A produced **9/30 VERIFIED results (30.0%)**, with a two-sided 95% Wilson
interval of **16.7%–47.9%**. **15/30 instances reached the deterministic judge
(50.0%)**; among those judged instances, **9/15 VERIFIED (60.0%)**, with a 95% Wilson
interval of **35.7%–80.2%**. **0/30 were PARTIAL**.

This is the first fix-coverage corpus that is repository-disjoint from v5, v6, and v7.
Its 30 mechanically selected instances span 17 repositories that were not examined while
the engine changes under test were developed. V8 therefore supplies the generalization
evidence that v7 could not. It is a pilot estimate, not a population constant.

## Probe-to-provider accounting

The provider-free probe reached the judge on 17/30 instances. The provider run reached
15/30: `moonshotai-kimi-cli-pr-1269` and `aden-hive-hive-pr-4869` had reachable
environments in the probe but hit the registered 720-second instance ceiling in the
provider run before reaching the judge. Every dependency-install and checkout outcome
otherwise agreed between the two phases.

| Measure | Probe | Provider run |
|---|---:|---:|
| Completed | 30/30 | 30/30 |
| Reached judge | 17/30 (56.7%) | 15/30 (50.0%) |
| VERIFIED | not measured | 9/30 (30.0%) |
| PARTIAL | not measured | 0/30 (0.0%) |
| Provider calls | 0 | 37 |
| Active wall time | 1,970.2 s | 6,580.2 s |

## Ranked non-VERIFIED taxonomy

| Rank | Outcome | Instances | Repositories | Share of 21 non-VERIFIED | Share of all 30 |
|---:|---|---:|---:|---:|---:|
| 1 | Environment dependency install failed | 10 | 7 | 47.6% | 33.3% |
| 2 | Candidate did not fail on buggy state | 3 | 3 | 14.3% | 10.0% |
| 2 | Candidate infrastructure failure | 3 | 3 | 14.3% | 10.0% |
| 2 | Checkout failed | 3 | 2 | 14.3% | 10.0% |
| 5 | Timed out | 2 | 2 | 9.5% | 6.7% |
| — | VERIFIED | 9 | 7 | — | 30.0% |
| — | PARTIAL | 0 | 0 | — | 0.0% |

The final report has `provider_unavailable: 0`, `unclassified: 0`, `complete: true`, and
`halted_reason: null`. Three explicit quota responses halted collection after 12, 14,
and 24 completed instances. Each next instance remained unattempted; the identical command
later resumed from checkpoints, and no completed outcome was rerun.

## Dependency-install breakdown

The run used Linux/arm64 Docker sandboxes on a Darwin/arm64 host. Docker exposed
4,109,803,520 bytes (3.83 GiB), so memory-sensitive failures remain scoped to this host.

| Category | Count | Share of 10 install failures |
|---|---:|---:|
| Pinned distribution unavailable | 5 | 50.0% |
| Other install failure | 3 | 30.0% |
| Package build backend or metadata failure | 2 | 20.0% |
| All other registered install categories | 0 | 0.0% |

`other_install_failure` exceeds the classifier's 20% warning threshold. The bucket is too
coarse to support a precise claim about the next environment constraint. The registered
no-fix rule was honored: neither taxonomy nor engine changed during v8, and the warning is
published intact. Raw dependency diagnostics remain private.

## The nine proofs

Every proof came from the frozen mechanical order; none was selected after outcomes were
seen.

1. [`aden-hive/hive#5058`](https://github.com/aden-hive/hive/pull/5058) proved that the
   deprecated Worker–Judge planning modules are no longer shipped as importable production
   modules.
2. [`darknessomi/musicbox#972`](https://github.com/darknessomi/musicbox/pull/972) proved
   that an exact visible song-name match ranks ahead of an unrelated earlier result during
   in-place search.
3. [`sensepost/objection#788`](https://github.com/sensepost/objection/pull/788) proved
   that operating-system warning logic tolerates a newly constructed device state without
   raising `AttributeError` or emitting a warning.
4. [`yusufkaraaslan/Skill_Seekers#294`](https://github.com/yusufkaraaslan/Skill_Seekers/pull/294)
   proved that default-parser web routing tolerates a namespace without the web-only
   `max_pages` attribute.
5. [`maurosoria/dirsearch#1577`](https://github.com/maurosoria/dirsearch/pull/1577)
   proved that explicit virtual-environment Python selection preserves the selected
   symlink rather than dereferencing it to the base interpreter.
6. [`huggingface/ml-intern#39`](https://github.com/huggingface/ml-intern/pull/39) proved
   that Hugging Face Router authentication falls back to the CLI's `HF_TOKEN` when
   `INFERENCE_TOKEN` is absent.
7. [`droidrun/mobilerun#289`](https://github.com/droidrun/mobilerun/pull/289) proved the
   seven-attempt state-retrieval budget and accessibility recovery ordering.
8. [`huggingface/ml-intern#46`](https://github.com/huggingface/ml-intern/pull/46) proved
   that the particle-logo text says `ML INTERN`, not the stale `ML AGENT` label.
9. [`darknessomi/musicbox#974`](https://github.com/darknessomi/musicbox/pull/974) proved
   that requesting song information with no current song does not open a browser page.

Each VERIFIED Case records `truth.execution: COMPLETED`, `silence_reason: null`, five
matching buggy-state failures, and a fixed-state pass. `minimize_verified` was false, as
registered.

## Time, provider, and provenance

- Collection interval: September 8, 2026 02:21:47 UTC through September 10, 2026
  16:32:01 UTC (62h 10m 14s), including quota waits.
- Sum of measured instance-worker time: 6,580.2 seconds (1h 49m 40s).
- Provider: `openai-codex-cli`; requested model: `gpt-5.6-sol`.
- Confirmed model and version: `unknown_no_telemetry`.
- Normalized proposal/refinement calls: 37.
- Actual model spend and token totals: unavailable because the CLI reported neither
  complete usage nor billable cost. This is not `$0` and is not estimated.
- Probe execution revision: `e4a6be0d61400bf013e7f589cc5986faaa2cc61f`.
- Provider execution revision: `5fc98e04087dca6c7911001a9e46401baef9900d`.
  The intervening commit froze only the probe artifacts; engine code was unchanged.

The privacy-filtered [`public-report.json`](./public-report.json) has SHA-256
`d5bca6b3b61b26bbdd3785b4d05f44846ee93db6d2fefba7864fb44251a7948a`.
It binds corpus SHA-256
`edfbd4f423901b8729eaad6210c87c2d3ad5765610a04090f1d6defc0cc838af`,
preregistration SHA-256
`cba96e1865975e207cc8ad47dde9acd50c11e5d23bb499a545a33d998fc6a5a3`,
and private-report SHA-256
`e00aac8970505b3e752e8d969a4670a74c66e6f80bbc6ee32b973a4042236ef9`.
Raw Cases, generated tests, dependency text, provider diagnostics, and execution logs remain
private.

## Honest read

V8 is evidence of generalization: on repositories excluded whole from every earlier
corpus, Exhibit A proved 30% of mechanically selected fixes end to end and 60% of the
instances its judge could inspect. That is a credible narrow-product result, not merely a
judge demonstration.

It is not broad coverage. Half the selected fixes never reached the judge, the 95%
interval is wide, the corpus contains only 30 instances on one architecture, and the
install taxonomy's catch-all is too large. The defensible positioning is a
**narrow, silence-tolerant product backed by research instrumentation**—not a general
Python bug verifier.
