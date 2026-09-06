# Pilot v7 provider-free reach probe

**15/30 instances reached the deterministic judge (50.0%).** The other 15 failed
during dependency installation. This exceeds the preregistered provider-run gate of
3/30, so pilot v7 proceeds to a real-provider run on this exact corpus and engine.

This is a plumbing result, not a verification result. `--probe` uses the built-in
stub proposer, makes no provider calls, and cannot produce VERIFIED evidence. All 30
probe Cases are therefore UNCERTAIN by design; the 15 judge-reaching candidates were
rejected as vacuous.

## Result

| Measure | Result |
|---|---:|
| Included and completed | 30/30 |
| Reached judge | 15/30 (50.0%) |
| Dependency installation failed | 15/30 (50.0%) |
| Provider calls | 0 |
| Active wall time | 2,417.97 s (40m 18s) |
| Calendar interval | 1h 35m 33s |
| Complete / halted | yes / no |

The v7 corpus is disjoint from v5 and v6. For context only, the post-fix probe on the
frozen, optimistically biased v6 corpus reached 13/30; v7 reached 15/30 on unseen PRs.
The corpora are not paired, so the two-instance difference is descriptive, not a causal
estimate.

## Dependency-install breakdown

The probe ran on a Darwin/arm64 host with Linux/arm64 Docker sandboxes.

| Public category | Count | Share of 15 install failures |
|---|---:|---:|
| Package build backend or metadata failure | 7 | 46.7% |
| Native distribution build failure | 5 | 33.3% |
| Artifact hash or integrity failure | 1 | 6.7% |
| Dependency resolution conflict | 1 | 6.7% |
| Pinned distribution unavailable | 1 | 6.7% |
| Other install failure | 0 | 0.0% |
| Package index or network failure | 0 | 0.0% |
| Python version incompatible | 0 | 0.0% |

Raw installation diagnostics remain private. No catch-all refinement was needed, and no
environment or classifier change was made in response to these results.

## Reproducibility and operations note

- Corpus SHA-256:
  `f0cc23ecd53c4289da248904a9f38f90de56a9f7613f4e7c80704fe0d2d74282`
- Preregistration SHA-256:
  `319b1112c0868b7377d340e5e0a863b23ad8dbce8830786436507ec893c089ae`
- Execution source revision:
  `546b3b2388a0ea54dad1598087935a4614098404`
- Public report SHA-256:
  `d69182dc54f9dde4b2502617d2f78c509419479cb3b85955707cb2b211752c84`

The host repeatedly entered sleep during the first invocation. Its calendar elapsed time
therefore advanced while Python's active monotonic timeout did not. The parent was
manually interrupted after this was initially mistaken for a timeout defect, then the
identical command resumed from durable checkpoints under a keep-awake guard. The active
worker had already written its result; resume reused that result, so no completed outcome
was rerun. The final report is complete for all 30 instances.

The machine-readable, privacy-filtered result is
[`probe-report.json`](./probe-report.json). The corpus ID correction made before any
probe outcome is separately recorded in [`amendment-001.json`](./amendment-001.json).
