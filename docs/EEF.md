---
layout: default
title: Executable Evidence Format
---

# Executable Evidence Format (EEF) v2

EEF is Exhibit A's deterministic archive format for transporting signed evidence without
asking the recipient to trust a screenshot, model summary, or hosted service. A
bundle contains one claim payload, source snapshots, the exact pytest contract and argv,
a Dockerfile, content manifest, and an in-toto Statement-shaped attestation. EEF v2
supports bug-flip Cases and behavior-preserving refactor evidence. The verifier remains
backward-compatible with signed `eef/v1` bug bundles.

## Guarantees

- `verify` checks every payload size and SHA-256 hash entirely offline.
- The attestation signs the manifest with HMAC-SHA256. Verification therefore proves
  that the holder of the shared publisher key minted the bundle. Key distribution is
  deliberately outside EEF v2; this is not a public-key identity claim.
- `verify` also validates claim-specific structure. For refactor evidence it revalidates
  every run/receipt digest and linkage and re-derives the complete recorded truth from the
  signed outcomes. This detects internally inconsistent evidence without executing code.
- `verify --execute` builds with Docker networking disabled. Bug bundles submit fresh raw
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

The Docker base image and `pytest==8.4.1` must already exist in the local Docker
cache for offline re-execution. The reference `python:3.12-slim` tag is not yet bound
to an OCI digest, so the local image cache remains an explicit replay trust boundary.
EEF v2 does not embed OCI layers. Repository source
snapshots exclude `.git`, `.exhibit-a`, `__pycache__`, and `.env`; publishers must
still review bundles for repository-specific secrets before sharing them. EEF is a
private/full-fidelity evidence archive, not a sanitized public passport. Refactor bundles
also retain bounded local executor image handles so signed request digests can be
recomputed; URL-, path-, and userinfo-shaped handles are rejected, but publishers must
still treat all source and log content as private.

Use `exhibit-a passport` to derive a verified, credential-free public JSON projection
instead of publishing the private EEF directly. The passport omits source, test/contract
code, raw logs, local paths, and free-form narratives while retaining the signed manifest
root, truth separation, state summaries, receipt digests, and model-identity commitments. See
the [public evidence passport](./PASSPORT.html).

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

python3 -m exhibit_a.cli passport case.eef \
  --signing-key /secure/eef.key --out case.passport.json

python3 -m exhibit_a.cli passport-html case.passport.json \
  --signing-key /secure/eef.key --out case.passport.html
```

## Archive layout

```text
attestation.json       in-toto Statement + HMAC signature
case.json              canonical bug-flip Case (exactly one claim payload)
refactor.json          canonical refactor evidence (alternative claim payload)
manifest.json          SHA-256 and byte size of every signed payload
reproduce.json         claim-specific argv, budgets, tree digests, and expectations
Dockerfile             no-network replay environment
sources/target/**      target snapshot plus generated test/contract
sources/base/**        base snapshot plus generated test/contract
logs/**                 raw target/base/control/bisect/suite logs
```

`manifest.json` hashes all evidence payloads. `attestation.json` signs the canonical
statement whose subject is the manifest hash, following the in-toto subject/predicate
shape without claiming SLSA build provenance that the local reference runner does not
possess.
