---
layout: default
title: Cross-version evidence archaeology
---

# Cross-version evidence archaeology

Archaeology freezes the runnable test from a sealed PROVEN Case and executes it across
an explicit oldest-to-newest list of commit SHAs. It answers whether the behavior was
already broken before the review window, first appears between two observed revisions,
never appears, or changes non-monotonically. This differs from bisect: it produces a
release-history timeline, not a single culprit on one branch.

```bash
cd engine
python3 -m exhibit_a.cli archaeology case.json \
  https://github.com/org/repo.git \
  --sha <oldest> --sha <next> --sha <newest>
```

Every SHA is validated before Git runs. The existing checkout intake accepts HTTPS only,
passes Git arguments as argv, disables hooks, and cleans its temporary checkout after
each revision. Docker remains network-disabled during test execution. Each revision is
classified as pass, matching failure, other failure, flaky, or infrastructure failure.

Reports use `evidence-archaeology/v1`. `pre_existing_before_window` is an attribution in
this study report; it is intentionally not a new Case verdict and cannot replace the
deterministic flip judge. The exact tested SHA list is the scope boundary: archaeology
does not claim knowledge before the oldest observed commit.
