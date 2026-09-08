# Pilot v8 provider-free reach probe

**17/30 instances reached the deterministic judge (56.7%).** The result clears the
preregistered provider-run gate of 3/30, so pilot v8 proceeds to a real-provider run on
this exact corpus and engine.

This is a plumbing result, not a verification result. `--probe` uses the built-in stub
proposer, makes no provider calls, and cannot produce VERIFIED evidence. The 17
judge-reaching candidates were rejected as vacuous by design.

## Result

| Measure | Result |
|---|---:|
| Included and completed | 30/30 |
| Reached judge | 17/30 (56.7%) |
| Dependency installation failed | 10/30 (33.3%) |
| Checkout failed | 3/30 (10.0%) |
| Provider calls | 0 |
| Active wall time | 1,970.23 s (32m 50s) |
| Calendar interval | 32m 59s |
| Complete / halted | yes / no |

V8 is repository-disjoint from v5, v6, and v7: its 30 instances span 17 repositories
that were not examined while the engine changes under test were built. The probe says
that the provider phase can exercise the judge on this fresh-repository corpus. It says
nothing about how many fixes the provider will verify.

## Dependency-install breakdown

The probe ran on a Darwin/arm64 host with Linux/arm64 Docker sandboxes. Docker exposed
4,109,803,520 bytes (3.83 GiB) to the daemon, so memory-sensitive environment failures
remain scoped to this machine.

| Public category | Count | Share of 10 install failures |
|---|---:|---:|
| Pinned distribution unavailable | 5 | 50.0% |
| Other install failure | 3 | 30.0% |
| Package build backend or metadata failure | 2 | 20.0% |
| Artifact hash or integrity failure | 0 | 0.0% |
| Dependency resolution conflict | 0 | 0.0% |
| Native distribution build failure | 0 | 0.0% |
| Package index or network failure | 0 | 0.0% |
| Python version incompatible | 0 | 0.0% |

`other_install_failure` is 30% of install failures, above the report's 20% warning
threshold. The category is therefore too coarse for interpreting the next environment
constraint. The preregistered no-fix rule forbids changing the taxonomy during v8, so
the warning is published rather than refined in this run. Raw installation diagnostics
remain private.

## Reproducibility

- Corpus SHA-256:
  `edfbd4f423901b8729eaad6210c87c2d3ad5765610a04090f1d6defc0cc838af`
- Preregistration SHA-256:
  `cba96e1865975e207cc8ad47dde9acd50c11e5d23bb499a545a33d998fc6a5a3`
- Execution source revision:
  `e4a6be0d61400bf013e7f589cc5986faaa2cc61f`
- Public probe report SHA-256:
  `8e5ae715140134373dc66b3c318fb4c719c78640f3f29ddc265ddd26857573ea`

The machine-readable, privacy-filtered result is
[`probe-report.json`](./probe-report.json).
