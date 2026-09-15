# Workflows

| Workflow | Trigger | Spends | Secret |
|---|---|---|---|
| `ci.yml` | push, pull request | nothing | none |
| `reach-probe.yml` | manual | nothing | none |
| `coverage-pilot.yml` | manual, confirmed | model calls | `EXHIBIT_A_PROVIDER_KEY` |

## Why the studies run here

Both agent sessions working on this repository share a workstation with 8 GiB of memory
that gives Docker 3.83 GiB. A thirty-instance study builds thirty environment images of
0.7-2 GB each, and two pilot v7 instances have already died with `cannot allocate memory`
and been recorded, wrongly, as environment failures. That limit cannot be raised on an
8 GiB machine, so studies run on a hosted runner instead.

It is also the only x86_64 evidence this project has. Pilots v5 through v8 all recorded
`linux/arm64`, and the first probe run here reached the judge on 24 of 30 instances where
the same corpus reached 17 on arm64 — two of them blocked by distributions that have no
arm64 build at all. Platform is recorded in every report for that reason.

## Running a pilot

1. Run `reach-probe` on the corpus first. It is free, needs no credential, and a corpus
   that does not reach the judge will not reach it with a model attached either.
2. Add the provider credential as the `EXHIBIT_A_PROVIDER_KEY` repository secret.
3. Dispatch `coverage-pilot` with the corpus path and `confirm` set to `spend`.

The confirmation gate is the first step and runs before checkout, so a misdispatch costs
nothing. The credential is never written to the provider configuration file: the file
names the environment variable and the engine reads it at request time, which is the
contract every adapter uses. The configuration is printed in the log so that is checkable.

Neither workflow is reachable from a pull request. Both are `workflow_dispatch` only with
`contents: read`, so a fork cannot obtain the secret by proposing a change to them.

A run that exhausts its ceiling exits 1 with instances remaining and uploads its
checkpoints; dispatching again with the same corpus resumes rather than restarts.
