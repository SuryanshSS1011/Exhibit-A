# Exhibit A CI release-truth dogfood

This example combines two conclusions that deliberately have different scopes:

- the deterministic bug judge says `VERIFIED` because the timeout-verdict regression test
  fails three times on the buggy snapshot and passes on the assessed release revision;
- the release-policy judge says `SAFE` because the pinned release revision's `engine` and
  `web` checks were both observed as `completed`/`success` under the checked policy.

Neither conclusion implies the other. `SAFE` records only that the named checks matched
the policy at collection time. It does not prove that the code is correct, and it does not
create or strengthen the underlying bug verdict.

## Pinned inputs

- buggy timeout-verdict revision: `3c3ec8996383750423f6f32d398850cd7af889e5`
- assessed release revision:
  [`de669e7e09aa5694911fe524ab30253f75a6b5cc`](https://github.com/SuryanshSS1011/Exhibit-A/commit/de669e7e09aa5694911fe524ab30253f75a6b5cc)
- normalized observation: [`ci_status.receipt.json`](./ci_status.receipt.json)
- named policy: [`release-policy.json`](./release-policy.json)

The read-only connector collected the public GitHub check-run response on
`2026-09-02T21:07:18.553485+00:00`. GitHub reported five completed successful checks. The
policy names only `engine` and `web`; the other three checks remain in the full-fidelity
receipt for completeness but the public passport exposes only an omitted-count of three.
The receipt retains a digest of the exact raw response rather than the raw response body.

The 31-day policy window is part of this historical statement: the checks were about 15
days old when collected. Recollecting later would be a new observation and may produce a
different freshness result. It must not silently replace this fixture. Routine generation
and tests perform no network access and use the checked receipt as their sole CI input.

The release revision is later than the original `1f9473f` timeout fix and still contains
that fix. The executor's `target`/`base` labels retain the flip judge's fail-on-bug and
pass-on-fix vocabulary; the Case revisions retain chronological buggy/release history.

## Reproduce offline

```bash
cd engine
PYTHONPATH=. uv run --with pytest==9.1.1 -- \
  python3 ../examples/dogfood/exhibit_a_ci/generate.py --check
```

The generator replays the historical fail-to-pass boundary, validates the frozen receipt,
re-derives release truth at its recorded observation instant, creates a temporary private
EEF v3, and byte-compares the checked JSON and HTML passports. The exact pytest version is
pinned because pytest's failure rendering is signed input.

The fixed `DEMO_KEY` in `generate.py` is intentionally public. It makes reproduction and
standalone MAC verification possible; it proves neither publisher identity nor that GitHub
authored the receipt. The source-bearing EEF remains temporary and is not published.

Published artifacts:

- [`exhibit_a_ci.passport.json`](./exhibit_a_ci.passport.json)
- [`exhibit_a_ci.passport.html`](./exhibit_a_ci.passport.html)
