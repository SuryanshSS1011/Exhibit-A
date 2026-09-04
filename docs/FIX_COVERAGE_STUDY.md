---
layout: default
title: Real-fix coverage study
---

# Real-fix coverage study

`fix-coverage-study/v1` measures a different question from `study`: across a mechanically
selected corpus of real fixes, how often does the current engine reach **VERIFIED**?
`study` asks whether repeated investigations of one claim converge. Fix coverage asks how
much of the stated problem class the product can prove at all.

This is observation-only research instrumentation. It imports the ordinary engine and
Docker executor, and it cannot influence a Case verdict. Nothing under `verdict/` imports
it. VERIFIED still means the deterministic judge observed the same claim-matching failure
on every buggy-state rerun and a pass on the fixed state. PARTIAL is counted separately.

## Pre-register, select, then run

The original pilot method is checked in at
[`studies/fix-coverage/pilot-v1/preregistration.json`](https://github.com/suryanshss1011/Exhibit-A/blob/main/studies/fix-coverage/pilot-v1/preregistration.json).
It fixes the sample size, dates, ordering, exclusions, model, budgets, retry policy, and
taxonomy before the first outcome is observed. That first frame produced no instances:
45 of the top 50 repositories did not pass pinned-environment eligibility, four had no
matching PR, and one clone failed. The result is recorded rather than called “0%.”

Before any model call or verdict, pilot v2 amended only the repository frame: scan at most
1,000 star-ranked repositories and retain the first 50 accepted by the current pinned
environment loader. That produced only five candidates, all from one repository, because
49 of the 50 used no exact PR-level `bug` label. Pilot v3 therefore preregistered a
pre-outcome amendment: an exact `bug` label or a title beginning with
`fix`/`fixed`/`fixes`/`fixing`, within the first 100 date-filtered results per repository.
That selected 16 instances before 50 candidate-detail reads failed during a transient
`No route to host` outage. Pilot v4 keeps the frame unchanged and adds five bounded
metadata-read attempts only.
Corpus selection is executable:

```bash
cd engine
python3 -m exhibit_a.cli select-fix-corpus \
  --preregistration ../studies/fix-coverage/pilot-v4/preregistration.json \
  --date-from 2026-02-17 --date-to 2026-08-31 \
  --repositories 50 --repository-scan-limit 1000 \
  --instances 30 --per-repository-cap 5 \
  --out ../studies/fix-coverage/pilot-v4/corpus.json
```

Selection uses GitHub's public API and Git transport. `GITHUB_TOKEN` is optional and is
read only from the environment; it is never written to the manifest. Cached selection
data stays under `.exhibit-a/`. Every selected instance pins the repository URL, buggy
first-parent SHA, fixing SHA, verbatim PR-title claim, commit date, and PR URL. Repository
and candidate exclusions are preserved in the manifest.

Run the registered pilot from `engine/`:

```bash
python3 -m exhibit_a.cli fix-coverage \
  ../studies/fix-coverage/pilot-v4/corpus.json \
  --model gpt-5.6-sol \
  --instance-timeout-s 720 --total-ceiling-s 21600 \
  --execution-timeout-s 120 --reruns 5 --max-refine 3 \
  --out ../.exhibit-a/research/fix-coverage/pilot-v4
```

Each instance runs in a separate process group. A hard per-instance ceiling can terminate
the model, checkout, container build, and test descendants together. The parent writes an
atomic checkpoint after every completed item and reconstructs the aggregate from those
checkpoints. Running the identical command again skips completed items; parameter or
manifest drift is rejected. An interrupted, incomplete attempt remains recorded and does
not silently become an outcome retry.

## What the report preserves

The private `report.json` carries the versioned schema, engine version, manifest hash,
run configuration, dates, requested and confirmed model identity, proposal-call telemetry,
active wall time, every terminal category, and per-instance summary. Raw Cases, generated
tests, and execution logs remain in private worker checkpoints because they may contain
unreviewed third-party content.

The headline denominator includes every included instance, including failures to build,
missing services, proposal silence, rejected candidates, flakes, and timeouts. Selection
losses appear alongside the headline: repository-level exclusions, candidate exclusions,
and eligible candidates outside the fixed sample are not hidden. The report ranks failure
categories and warns if catch-all categories exceed the preregistered 20% threshold.

Provider cost is reported only when every model call reports billable cost. The Codex CLI
does not currently expose that telemetry, so its honest spend result is **unavailable**,
not `$0`. Requested identity is still recorded, while confirmed identity remains the
explicit `unknown_cli_no_telemetry` value.

## Pilot result

Pilot v1 stopped at selection with zero instances, pilot v2 stopped with five, and pilot
v3 stopped with 16 after a transport outage. No Exhibit A outcome belonged here when
pilot v4 was preregistered. Its corpus, aggregate, and interpretation are added after the
run without rewriting those facts or dropping failed instances.
