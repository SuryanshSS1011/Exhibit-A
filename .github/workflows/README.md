# Workflows

| Workflow | Trigger | Spends | Secret |
|---|---|---|---|
| `ci.yml` | push, pull request | nothing | none |
| `reach-probe.yml` | manual | nothing | none |
| `coverage-pilot.yml` | manual, confirmed | model calls | `EXHIBIT_A_PROVIDER_KEY` |

## Why the probe runs here and pilots do not

Pilots run on the workstation, on the ChatGPT subscription that already pays for the
model. An API key would bill a second time for that, and transporting the Codex credential
cache to a runner is worse: it holds access and refresh tokens, so it is standing account
access rather than a scoped secret, and the runner refreshes it mid-run. An earlier
version of this file sent paid pilots here on the strength of an 8 GB memory rule that has
since been removed: pilot v8 completed all thirty instances on that 3.83 GiB workstation
and none of its environment failures was memory.

The probe runs here because it is free to run anywhere and this is the only x86_64
evidence this project has. Pilots v5 through v8 all recorded
`linux/arm64`, and the first probe run here reached the judge on 24 of 30 instances where
the same corpus reached 17 on arm64 — two of them blocked by distributions that have no
arm64 build at all. Platform is recorded in every report for that reason.

## Running a pilot

Pilots run on the workstation with the Codex CLI. `coverage-pilot.yml` remains for a
hosted provider that bills separately, and is not the default path: it needs the
`EXHIBIT_A_PROVIDER_KEY` secret and `confirm` set to `spend`, and it does not support
`codex_cli`, whose adapter authenticates from a session rather than a named variable.

Run `reach-probe` on any corpus first either way. It is free, needs no credential, and a
corpus that does not reach the judge without a model will not reach it with one.

The confirmation gate is the first step and runs before checkout, so a misdispatch costs
nothing. The credential is never written to the provider configuration file: the file
names the environment variable and the engine reads it at request time, which is the
contract every adapter uses. The configuration is printed in the log so that is checkable.

Neither workflow is reachable from a pull request. Both are `workflow_dispatch` only with
`contents: read`, so a fork cannot obtain the secret by proposing a change to them.

A run that exhausts its ceiling exits 1 with instances remaining and uploads its
checkpoints; dispatching again with the same corpus resumes rather than restarts.
