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
`No route to host` outage. Pilot v4 kept the frame unchanged and added five bounded
metadata-read attempts only. Pilot v5 selects a fresh corpus: it holds that public source
frame constant but reruns eligibility with `uv.lock` support, makes judge reach the primary
metric, halts on provider quota instead of recording a failed instance, and preregisters a
category-only dependency-install breakdown.

Corpus selection is executable:

```bash
cd engine
python3 -m exhibit_a.cli select-fix-corpus \
  --preregistration ../studies/fix-coverage/pilot-v5/preregistration.json \
  --date-from 2026-02-17 --date-to 2026-08-31 \
  --repositories 50 --repository-scan-limit 1000 \
  --instances 30 --per-repository-cap 5 \
  --cache ../.exhibit-a/research/fix-coverage-selection-cache-v2 \
  --out ../studies/fix-coverage/pilot-v5/corpus.json
```

Selection uses GitHub's public API and Git transport. `GITHUB_TOKEN` is optional and is
read only from the environment; it is never written to the manifest. Cached selection
data stays under `.exhibit-a/`. Every selected instance pins the repository URL, buggy
first-parent SHA, fixing SHA, verbatim PR-title claim, commit date, and PR URL. Repository
and candidate exclusions are preserved in the manifest.

Run the registered pilot from `engine/`:

```bash
python3 -m exhibit_a.cli fix-coverage \
  ../studies/fix-coverage/pilot-v5/corpus.json \
  --model gpt-5.6-sol \
  --instance-timeout-s 720 --total-ceiling-s 21600 \
  --execution-timeout-s 120 --reruns 5 --max-refine 3 \
  --out ../.exhibit-a/research/fix-coverage/pilot-v5
```

Each instance runs in a separate process group. A hard per-instance ceiling can terminate
the model, checkout, container build, and test descendants together. The parent writes an
atomic checkpoint after every completed item and reconstructs the aggregate from those
checkpoints. Running the identical command again skips completed items; parameter or
manifest drift is rejected. An interrupted, incomplete attempt remains recorded and does
not silently become an outcome retry. An explicit provider-quota response halts the run
without checkpointing that corpus row. Resume after capacity returns creates a new worker
attempt for the still-unattempted row and retains the quota attempt in the private audit
trail.

## What the report preserves

The private `report.json` carries the versioned schema, engine version, manifest hash,
run configuration, dates, requested and confirmed model identity, proposal-call telemetry,
active wall time, every terminal category, and per-instance summary. Raw Cases, generated
tests, and execution logs remain in private worker checkpoints because they may contain
unreviewed third-party content.

The primary metric is `reached_judge_fraction`: the share of all included instances where
a candidate reached the deterministic judge. `verified_fraction_of_judged` then reports
the conditional result, while the whole-pipeline VERIFIED fraction keeps all included
instances in its denominator. PARTIAL is always separate. Selection losses appear beside
these measures: repository exclusions, candidate exclusions, and eligible candidates
outside the fixed sample are not hidden. The report ranks failure categories, separately
classifies dependency-install failures without publishing raw reasons, and warns if a
catch-all exceeds the preregistered 20% threshold.

Provider cost is reported only when every model call reports billable cost. The Codex CLI
does not currently expose that telemetry, so its honest spend result is **unavailable**,
not `$0`. Requested identity is still recorded, while confirmed identity remains the
explicit `unknown_no_telemetry` value.

After a completed run, export the reviewed, log-free public record separately from the
private checkpoints:

```bash
python3 -m exhibit_a.cli fix-coverage-report \
  ../studies/fix-coverage/pilot-v5/corpus.json \
  ../.exhibit-a/research/fix-coverage/pilot-v5 \
  --execution-source-revision 445f4982a3d93ec6e10e96adf4e038bcc98798ab \
  --execution-segments ../studies/fix-coverage/pilot-v5/execution-segments.json \
  --out ../studies/fix-coverage/pilot-v5/public-report.json
```

The exporter requires a complete run and matching corpus hash. It carries aggregate and
per-instance outcomes, selection losses, both failure taxonomies, the dependency-install
breakdown, run dates, engine/source versions, and provider identity telemetry. A normal
run records one source revision. The optional versioned execution-segments file records
contiguous corpus ranges when an audited runtime amendment occurs mid-run. The exporter
deliberately omits generated test bodies, execution logs, raw dependency errors, provider
diagnostics, and private filesystem locations.

## Pilot result

Pilot v1 stopped at selection with zero instances, pilot v2 stopped with five, and pilot
v3 stopped with 16 after a transport outage. Pilot v4 reached the judge on 1/30 and
VERIFIED that one case. The fresh v5 corpus reached the judge on **2/30 (6.7%)** and
VERIFIED both judged cases; **0/30 were PARTIAL**. Dependency installation blocked 24/30,
so the result remains a verdict on environment reach more than proposer or judge quality.
Read the [complete v5 result, ranked taxonomy, dependency breakdown, constraints, and
limitations](./FIX_COVERAGE_RESULTS.html), or the preserved
[v4 report](./FIX_COVERAGE_V4_RESULTS.html).
