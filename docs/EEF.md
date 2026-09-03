---
layout: default
title: Executable Evidence Format
---

# Executable Evidence Format (EEF)

EEF is Exhibit A's deterministic archive format for transporting signed evidence without
asking the recipient to trust a screenshot, model summary, or hosted service. A
bundle contains one claim payload, source snapshots, the exact pytest contract and argv,
a Dockerfile, content manifest, and an in-toto Statement-shaped attestation. EEF v2
supports bug-flip Cases and behavior-preserving refactor evidence. EEF v3 adds claim-bound
remote connector receipts without changing either deterministic claim judge. Minting
remains byte-compatible v2 when no remote receipt is supplied; the verifier reads signed
v1, v2, and v3 archives. EEF v4 adds an optional public-signature path using the
DSSE/Ed25519 profile and externally anchored trust policy defined by
[ADR 0001](./adr/0001-eef-v4-public-key-signatures.html).

## Guarantees

- `verify` checks every payload size and SHA-256 hash entirely offline.
- The attestation signs the manifest with HMAC-SHA256. Verification therefore proves
  that the holder of the shared publisher key minted the bundle. Key distribution is
  deliberately outside EEF v2; this is not a public-key identity claim.
- EEF v4 instead verifies with public key material: publishing its trust root does not
  grant signing authority. The external trust anchor pins the exact root, rollback floor,
  and expected policy before the DSSE payload is parsed. This proves attribution under
  that installed policy—not evidence correctness or a legal identity.
- `verify` also validates claim-specific structure. For refactor evidence it revalidates
  every run/receipt digest and linkage and re-derives the complete recorded truth from the
  signed outcomes. This detects internally inconsistent evidence without executing code.
- V3 stores normalized point-in-time CI payloads and their complete provenance receipts in
  `connector_receipts.json`. The canonical section is capped at 1 MiB and 32 receipts, each
  with at most 250 checks. Every receipt is bound to the SHA-256 of the exact claim payload,
  repository identity, full target revision, normalized request and response, source
  commitment, observation/update times, freshness basis, and request/response/content
  digests. Verification recomputes all three normalized digests entirely offline. The raw
  forge response is deliberately omitted, so `artifact_sha256` remains a signed commitment
  to the collected bytes rather than an independently rehashable body. Omission, duplicate
  evidence IDs, source-origin disagreement, digest disagreement, noncanonical encoding, or
  receipt substitution across claims fails closed. Structurally invalid evidence remains
  invalid even when re-signed, but a trusted HMAC holder can author a different coherent
  receipt; EEF does not independently authenticate the forge response.
- For bug claims carrying `release_evidence/v1`, offline verification also requires exactly
  one archived CI receipt and re-runs the named `release-policy/v1` at the signed explicit
  evaluation instant. The Case record contains the policy, receipt evidence ID, and time,
  but not a duplicate result. The verifier compares its derived release truth and reason
  with the signed truth fields. An assessed claim without its receipt, or a changed
  policy/truth/receipt combination, fails closed even when validly re-signed.
- `verify --execute` builds with Docker networking disabled and Docker pulling disabled.
  Bug bundles submit fresh raw
  outcomes to the unchanged `flip_check`. Refactor bundles repeat both archived states and
  compare the newly derived complete result with the recorded result. Replay can therefore
  confirm a reproducible `FAILED` claim as well as a `VERIFIED` one.
- Replay accepts only the verifier's byte-exact generated Dockerfile and fixed-shape
  pytest argv. A signed archive cannot substitute its own build instructions or pytest
  options.
- ZIP entries must be canonical, uncompressed regular files. Verification rejects path
  aliases, parent/file collisions, encrypted or compressed entries, more than 10,000
  entries, any entry above 64 MiB, and archives above 512 MiB before reading payloads.
- Reruns are strict integers from 1 through 20. Docker builds have a five-minute host
  timeout; test runs have a two-minute timeout plus CPU, memory, and PID limits. Captured
  bug-replay output is capped at 8 MiB per stream. `refactor-bundle` signs a 64 KiB
  per-stream collection/replay cap so all 40 possible runs remain within the 64 MiB
  claim-entry limit. Earlier refactor v2 bundles without this metadata retain the prior
  8 MiB replay default. Named replay containers are force-removed, and verifier-created
  images use per-run names and are cleaned up.
- ZIP entries are sorted, uncompressed, timestamped at the ZIP epoch, and assigned a
  fixed mode. Identical Case/source/key inputs produce byte-identical archives.

Public EEF v4 archives carry a canonical `eef-replay-environment/v1` descriptor. It binds
the replay image repository, OCI digest, operating system, architecture and optional
variant, plus pytest `8.4.1` and the SHA-256 of the exact pytest artifact installed in the
image. Before executing archived code, the verifier performs a local, digest-qualified
image inspection and compares the repository digest, platform and these image labels:
`dev.exhibit-a.pytest.version` and `dev.exhibit-a.pytest.artifact-sha256`. It will neither
resolve a mutable tag nor pull a missing image. Import the signed digest into Docker before
replay; a retagged image, wrong platform, missing labels or changed pytest artifact fails
closed. `lock-replay-environment` is the separate, explicit workflow for accepting a new
local digest and writing a descriptor. It also performs no pull, and refuses to replace an
existing file unless `--force` is supplied.

Legacy v1/v2/v3 archives retain their historical `python:3.12-slim` Dockerfile and
`pytest==8.4.1` installation behavior for compatibility; their mutable local image cache
remains an explicit replay trust boundary and must not be described as v4-grade replay
identity. EEF does not embed OCI layers. Repository source
snapshots exclude `.git`, `.exhibit-a`, `__pycache__`, and `.env`; publishers must
still review bundles for repository-specific secrets before sharing them. EEF is a
private/full-fidelity evidence archive, not a sanitized public passport. Refactor bundles
also retain bounded local executor image handles so signed request digests can be
recomputed; URL-, path-, and userinfo-shaped handles are rejected, but publishers must
still treat all source and log content as private.

Use `exhibit-a passport` to derive a verified, credential-free public JSON projection from
v1/v2 bundles instead of publishing the private EEF directly. Passport v2 also accepts an
assessed EEF v3 bug claim created by `release-evidence`. The public projections omit
source, test/contract code, raw logs, local paths, and free-form narratives while retaining
the signed manifest root, truth separation, state summaries, local execution-receipt digests,
and model-identity commitments. Remote v3 receipts remain private; passport v2 exposes only
the named policy, allowlisted required checks, timing/count summaries, digest commitments,
and re-derived release truth. See the [public evidence passport](./PASSPORT.html).

Integrity verification proves that the signed refactor evidence is internally coherent
and that the archived trees match their signed tree digests. Only `verify --execute`
establishes that fresh executions of those archived trees reproduce the recorded result;
an HMAC signature alone does not prove that historical receipts were originally produced
from the archived bytes.

## Reference commands

```bash
# Use a protected 32+ byte key file; do not commit it.
python3 -m exhibit_a.cli bundle case.json \
  --target-source /path/to/bad --base-source /path/to/good \
  --signing-key /secure/eef.key --out case.eef

# Execute a trusted before/after contract in the Docker sandbox and bundle its evidence.
python3 -m exhibit_a.cli refactor-bundle \
  --base-source /path/to/before --target-source /path/to/after \
  --contract /path/to/test_contract.py \
  --signing-key /secure/eef.key --out refactor.eef

python3 -m exhibit_a.cli verify case.eef --signing-key /secure/eef.key
python3 -m exhibit_a.cli verify case.eef --signing-key /secure/eef.key --execute

# Public-key profile (install the `public-signatures` extra first).
# First, intentionally lock a digest that is already present locally. The image must carry
# the two Exhibit A pytest identity labels described above; this command never pulls it.
python3 -m exhibit_a.cli lock-replay-environment \
  --reference registry.example/exhibit-a/replay-python \
  --digest sha256:<64-hex-image-digest> --architecture amd64 \
  --pytest-artifact-sha256 sha256:<64-hex-pytest-artifact-digest> \
  --out replay-environment.json

python3 -m exhibit_a.cli bundle-v4 case.json \
  --target-source /path/to/bad --base-source /path/to/good \
  --private-key /secure/ed25519.seed --trust-root trust-root.json \
  --policy-id eef-production-v1 --replay-environment replay-environment.json \
  --out case-v4.eef
python3 -m exhibit_a.cli verify-v4 case-v4.eef \
  --trust-root trust-root.json --trust-anchor trust-anchor.json

python3 -m exhibit_a.cli passport case.eef \
  --signing-key /secure/eef.key --out case.passport.json

python3 -m exhibit_a.cli passport-html case.passport.json \
  --signing-key /secure/eef.key --out case.passport.html

# Collect one pinned CI observation and stage EEF v3 plus passport v2 outputs.
python3 -m exhibit_a.cli release-evidence case.json \
  --target-source /path/to/target --base-source /path/to/base \
  --repository owner/name --revision 0123456789abcdef0123456789abcdef01234567 \
  --policy release-policy.json --evaluated-at 2026-09-02T12:00:00+00:00 \
  --token-env EXHIBIT_A_GITHUB_TOKEN --signing-key /secure/eef.key \
  --eef-out release.eef --passport-json-out release.passport.json \
  --passport-html-out release.passport.html

# GitLab uses the same normalized policy and receipt contract.
python3 -m exhibit_a.cli release-evidence case.json \
  --forge gitlab --target-source /path/to/target --base-source /path/to/base \
  --repository owner/name --revision 0123456789abcdef0123456789abcdef01234567 \
  --policy release-policy.json --evaluated-at 2026-09-02T12:00:00+00:00 \
  --token-env EXHIBIT_A_GITLAB_TOKEN --signing-key /secure/eef.key \
  --eef-out release.eef --passport-json-out release.passport.json \
  --passport-html-out release.passport.html
```

## Archive layout

```text
attestation.json       legacy Statement + HMAC, or v4 DSSE in-toto Statement
case.json              canonical bug-flip Case (exactly one claim payload)
refactor.json          canonical refactor evidence (alternative claim payload)
connector_receipts.json  optional v3 normalized remote facts and provenance
manifest.json          SHA-256 and byte size of every signed payload
reproduce.json         claim-specific argv, budgets, tree digests, and expectations
environment.json       v4-only immutable OCI/platform/pytest replay identity
Dockerfile             generated no-network, no-pull replay environment
sources/target/**      target snapshot plus generated test/contract
sources/base/**        base snapshot plus generated test/contract
logs/**                 raw target/base/control/bisect/suite logs
```

`manifest.json` hashes all evidence payloads. `attestation.json` signs the canonical
statement whose subject is the manifest hash, following the in-toto subject/predicate
shape without claiming SLSA build provenance that the local reference runner does not
possess.
