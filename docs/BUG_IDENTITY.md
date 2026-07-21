# Execution-based bug identity

Text similarity does not decide whether two reports describe the same bug. The dedup
study revalidates each sealed PROVEN Case on its own target and fix, then runs test A on
fix B and test B on fix A. Only deterministic mutual passes count as an equivalent pair.
Infrastructure failures and flaky cross-results stay inconclusive.

The local `bug-identity-corpus/v1` manifest contains path-contained Case files and
checkouts:

```json
{
  "schema_version": "bug-identity-corpus/v1",
  "cases": [
    {"case": "cases/a.json", "target": "repos/a-bug", "fixed": "repos/a-fix"},
    {"case": "cases/b.json", "target": "repos/b-bug", "fixed": "repos/b-fix"}
  ]
}
```

```bash
cd engine
python3 -m exhibit_a.cli dedup ../corpus/manifest.json --docker
```

Reports use `bug-identity/v1`. The exact pair matrix is authoritative. Reported clusters
use conservative complete-link grouping: a Case joins a cluster only when it is mutually
equivalent with every member. Inconclusive pairs therefore never merge clusters. This is
a research view and cannot alter any underlying Case verdict.
