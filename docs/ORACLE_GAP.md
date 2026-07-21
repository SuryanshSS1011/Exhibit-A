---
layout: default
title: Oracle-gap probe
---

# Oracle-gap probe

The oracle-gap probe audits resolved SWE-bench-style instances by applying the existing
allowlisted Python mutation operators to disposable copies and rerunning the instance's
official fail-to-pass test. A surviving mutant is a concrete behavior change that the
official test did not reject. It is a benchmark-quality measurement, not an Exhibit A
verdict, and it never changes `flip_check.py`.

## Corpus format

The input is a local, versioned JSON manifest. Paths are relative to the manifest and
must stay inside its directory; this keeps the probe reproducible and network-free.

```json
{
  "schema_version": "swe-bench-oracle-corpus/v1",
  "instances": [{
    "instance_id": "owner__repo-123",
    "resolved": "checkouts/owner__repo-123",
    "test_path": "tests/test_regression.py",
    "source_paths": ["package/module.py"],
    "suspect_lines": {"package/module.py": [41, 42]}
  }]
}
```

`resolved` is the already-prepared post-fix checkout and `test_path` is the official
fail-to-pass test inside it. `command` may be supplied when the repository needs a
different pytest invocation. Commands are tokenized to argv by the executor; no shell is
used. General environment reconstruction remains outside this probe.

Run it with:

```bash
cd engine
python3 -m exhibit_a.cli oracle-gap ../data/manifest.json --docker
```

Reports use `oracle-gap/v1`, list every surviving mutation ID, and are private under
`.exhibit-a/research/oracle-gap` by default. The aggregate oracle gap is
`survived / (killed + survived)`; invalid mutants and non-passing baselines are reported
but excluded from that denominator.
