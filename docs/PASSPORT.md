---
layout: default
title: Public Evidence Passport
---

# Public evidence passports

The public passport is a deterministic, credential-free JSON projection of a verified
Executable Evidence Format bundle. `exhibit-a-passport/v1` supports EEF v1/v2
`bug_flip` and `behavior_preserving_refactor` claims. The versioned
`exhibit-a-passport/v2` projection is reserved for EEF v3 bug claims with an assessed,
receipt-backed release policy. V1 bytes and its MAC domain remain unchanged.

Passport creation verifies every EEF entry hash, the manifest root, the shared-key
publisher signature, and the complete claim-specific structure before emitting anything.
The passport records that verification, the manifest SHA-256 root, the signature value,
the separated execution/goal/release truth, deterministic state summaries, evidence
receipt digests, and hash-committed runtime-model identity telemetry. For bug claims,
creation reconstructs the signed execution records and runs them back through the
deterministic flip judge; a contradictory or inadmissible signed claim is rejected.

The public artifact carries its own HMAC-SHA256 over the canonical sanitized JSON, so it
can be checked with the shared publisher key without retaining the private EEF. It also
deliberately omits source snapshots, generated test or contract
source, raw execution logs, repository-local paths, and free-form claim/model narratives.
Provider, model, version, connector, and evidence identifiers are always replaced with a
`sha256:` commitment, except for the two explicit unknown-model sentinels. Case IDs are
also always represented by a SHA-256 commitment. Remote evidence source URLs are reduced
to a SHA-256 commitment after credential stripping; local sources become `local-checkout`.
The final encoded passport is capped at 1 MiB and is installed with an atomic replacement
that cannot truncate a hardlinked EEF or verification key.

Passport v3 is the public-key counterpart for EEF v4. Its sanitized JSON payload is
wrapped in a domain-separated DSSE envelope and verified with the external trust root and
anchor; the public verification material cannot forge a passport. It records the source
EEF publisher, verified key IDs, and exact root digest/version, but labels those as claims
made by the passport issuer unless the source EEF is independently supplied and verified.
The two identities remain separate in JSON and HTML even when their strings match.

Passport v2 adds an exact-shape `release_evidence` section. It publishes the named policy
and its SHA-256 commitment, only policy-required check names/states/timestamps, total and
omitted check counts, point-in-time freshness, request/response/artifact/content
commitments, and the deterministically derived release truth. It does not publish the
repository request, API URL, connector or evidence identity, optional check names, raw
response, description, security metadata, token environment name/value, or local paths.
Even the public explanation is independently derived: duplicate non-required jobs yield a
generic reason rather than disclosing their names. A standalone verifier checks the exact
shape, policy digest, counts, timestamps, required-check result, release label, and reason
before accepting the v2 MAC.

The signed JSON can also be rendered as a self-contained HTML chain-of-custody document.
The renderer verifies the passport MAC first, escapes every displayed value, embeds no
scripts or external assets, and sets a deny-by-default Content Security Policy. The HTML
is a view of the sanitized JSON—not a second source of truth—and includes the full signed
payload in a collapsible machine-record section. Encoded HTML output is capped at 2 MiB.

```bash
python3 -m exhibit_a.cli passport case.eef \
  --signing-key /secure/eef.key --out case.passport.json

python3 -m exhibit_a.cli passport-html case.passport.json \
  --signing-key /secure/eef.key --out case.passport.html

python3 -m exhibit_a.cli passport-v3 case-v4.eef \
  --private-key /secure/ed25519.seed --trust-root trust-root.json \
  --trust-anchor trust-anchor.json --policy-id passport-production-v1 \
  --out case.passport-v3.json
python3 -m exhibit_a.cli passport-html-v3 case.passport-v3.json \
  --trust-root trust-root.json --trust-anchor trust-anchor.json \
  --out case.passport-v3.html
```

The complete release workflow collects once, evaluates once at an explicit instant, and
stages all three outputs before installing them:

```json
{
  "schema_version": "release-policy/v1",
  "name": "required-ci",
  "required_checks": ["engine", "web"],
  "max_age_s": 3600
}
```

```bash
export EXHIBIT_A_GITHUB_TOKEN=... # read-only token; never pass it as an argument
python3 -m exhibit_a.cli release-evidence case.json \
  --target-source /path/to/target --base-source /path/to/base \
  --repository owner/name --revision 0123456789abcdef0123456789abcdef01234567 \
  --policy release-policy.json --evaluated-at 2026-09-02T12:00:00+00:00 \
  --token-env EXHIBIT_A_GITHUB_TOKEN --signing-key /secure/eef.key \
  --eef-out release.eef \
  --passport-json-out release.passport.json \
  --passport-html-out release.passport.html
```

The repository and revision must exactly match the Case, and the revision must be a full
lowercase 40-character SHA-1. Public GitHub uses its fixed HTTPS API origin; a non-GitHub
repository requires an explicit same-origin `--api-base`. Unsafe origins, credentials in
URLs, branch names, missing environment credentials, invalid policies, existing outputs,
colliding paths, and outputs inside source trees fail before collection. Exit `0` means
`SAFE`; `UNSAFE` or `UNCERTAIN` still produce reviewable artifacts and exit `1`; collection
or configuration failure produces no artifacts and exits `2`. Final publication uses
same-filesystem, no-overwrite links with rollback, so a destination created concurrently
is preserved and a reported installation failure does not leave a partial generated set.

The HMAC signature proves that the holder of the shared verification key minted the EEF;
it does not establish a public publisher identity. The passport carries the signature but
never the verification key. Each passport version has its own MAC domain, and both are
domain-separated from EEF signatures, so one version or artifact type cannot be replayed
as another. It also distinguishes signed integrity
verification from fresh execution: `execution_replayed` remains `null` because passport
creation is offline and does not execute archived code.

The private EEF remains the full-fidelity replay artifact. The JSON passport is the small,
reviewable public summary linked back to that archive through `manifest_sha256`.

## Historical dogfood example

The repository publishes a JSON and HTML passport for its own real
[timeout-verdict bug](https://github.com/SuryanshSS1011/Exhibit-A/tree/main/examples/dogfood/timeout_false_verified).
The generator pins the buggy and fixed commits, injects the historical regression test,
executes the fail-to-pass boundary, and keeps the private EEF temporary. Its deliberately
public demo HMAC key supports reproducibility only and makes no publisher-identity claim.

The repository also publishes a v2 [CI release-truth
example](https://github.com/SuryanshSS1011/Exhibit-A/tree/main/examples/dogfood/exhibit_a_ci).
It pins a normalized observation for public revision `de669e7`, re-derives the named
`engine`/`web` policy entirely offline, and byte-compares both public artifacts. The page
shows `VERIFIED` for the independently replayed timeout-bug claim and `SAFE` for the CI
policy at collection time. `SAFE` neither proves program correctness nor strengthens that
claim verdict.
