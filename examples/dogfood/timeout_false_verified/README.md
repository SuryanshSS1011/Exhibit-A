# Historical timeout-verdict dogfood

This example runs Exhibit A against its own real history. Before
[`1f9473f`](https://github.com/SuryanshSS1011/Exhibit-A/commit/1f9473f8d6940935ec45a41cb518d9038e0bea0e),
a timed-out target execution could pass the deterministic flip gate and be mislabeled as
bug evidence. The fix added an explicit timeout infrastructure-failure gate.

The generator injects a standalone form of the historical regression test into two
repository snapshots already present in local Git history:

- chronological base commit (buggy): `3c3ec8996383750423f6f32d398850cd7af889e5`
- chronological target commit (fixed): `1f9473f8d6940935ec45a41cb518d9038e0bea0e`
- EEF execution target (buggy) and execution base (fixed), matching the flip judge's
  fail-on-target/pass-on-base vocabulary
- command: `python3 -m pytest -x -q -q tests/test_timeout_false_verified.py`; the second
  quiet flag suppresses pytest's nondeterministic elapsed-time summary at capture time
- observed boundary: three deterministic failures on the buggy snapshot, one pass on the
  fixed snapshot

It then feeds those raw outcomes through the current deterministic flip judge, creates a
private EEF in a temporary directory, and publishes only its credential-free JSON and
HTML passports.

Generation uses Exhibit A's disposable-copy local executor because these two pinned
snapshots are trusted project history; it does not claim container isolation for this
run. The generated private EEF retains the network-disabled Docker replay harness, but
`execution_replayed` remains `null` because this publication step does not invoke Docker.

The passport reports `evidence_sources: 0`, and that is deliberate rather than a missing
field. The generator drives the executor directly instead of routing through
`LocalTestConnector`, because an `EvidenceProvenance` receipt carries a wall-clock
`observed_at` and a random `evidence_id`. Recording them truthfully would make the
published artifact differ on every run and destroy the byte-reproducibility this example
exists to demonstrate; pinning them to fixed values would publish a provenance claim that
never happened. Omitting the receipts is the only option that keeps both the artifact
reproducible and every field in it true. Engine-driven Cases, which are not required to be
byte-reproducible, do record connector provenance.

```bash
cd engine
PYTHONPATH=. uv run --with pytest==9.1.1 -- \
  python3 ../examples/dogfood/timeout_false_verified/generate.py

# Re-run the history boundary and byte-compare both checked-in artifacts.
PYTHONPATH=. uv run --with pytest==9.1.1 -- \
  python3 ../examples/dogfood/timeout_false_verified/generate.py --check
```

The generator rejects any other pytest version because pytest's raw failure renderer is
part of the signed log and therefore part of byte-level reproducibility. This publication
toolchain is distinct from the EEF replay Dockerfile's pytest `8.4.1`; the passport says
`execution_replayed: null` because Docker replay is not part of this generation step.

The fixed `DEMO_KEY` in `generate.py` is intentionally public and exists only to make the
sample byte-reproducible and its standalone passport MAC checkable. It provides no
publisher identity or production authenticity—anyone can mint with it. The private EEF
and source snapshots are deliberately not checked in.

Published artifacts:

- [`timeout_false_verified.passport.json`](./timeout_false_verified.passport.json)
- [`timeout_false_verified.passport.html`](./timeout_false_verified.passport.html)
