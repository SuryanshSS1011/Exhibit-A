# Executable Evidence Format (EEF) v1

EEF is Exhibit A's deterministic archive format for transporting a Case without
asking the recipient to trust a screenshot, model summary, or hosted service. A
bundle contains the canonical Case JSON, target/base source snapshots, generated
pytest file, exact argv, raw logs, a Dockerfile, content manifest, and an in-toto
Statement-shaped attestation.

## Guarantees

- `verify` checks every payload size and SHA-256 hash entirely offline.
- The attestation signs the manifest with HMAC-SHA256. Verification therefore proves
  that the holder of the shared publisher key minted the bundle. Key distribution is
  deliberately outside EEF v1; this is not a public-key identity claim.
- `verify --execute` builds with Docker networking disabled, runs the target for the
  recorded determinism count, runs the base when present, and submits the fresh raw
  outcomes to the unchanged `flip_check`. The verifier never trusts the recorded
  verdict as a substitute for execution.
- ZIP entries are sorted, uncompressed, timestamped at the ZIP epoch, and assigned a
  fixed mode. Identical Case/source/key inputs produce byte-identical archives.

The Docker base image and `pytest==8.4.1` must already exist in the local Docker
cache for offline re-execution. EEF v1 does not embed OCI layers. Repository source
snapshots exclude `.git`, `.exhibit-a`, `__pycache__`, and `.env`; publishers must
still review bundles for repository-specific secrets before sharing them.

## Reference commands

```bash
# Use a protected 32+ byte key file; do not commit it.
python3 -m exhibit_a.cli bundle case.json \
  --target-source /path/to/bad --base-source /path/to/good \
  --signing-key /secure/eef.key --out case.eef

python3 -m exhibit_a.cli verify case.eef --signing-key /secure/eef.key
python3 -m exhibit_a.cli verify case.eef --signing-key /secure/eef.key --execute
```

## Archive layout

```text
attestation.json       in-toto Statement + HMAC signature
case.json              canonical Case contract
manifest.json          SHA-256 and byte size of every signed payload
reproduce.json         argv, rerun count, expected signature, evidence tier
Dockerfile             no-network replay environment
sources/target/**      bad-state snapshot plus generated test
sources/base/**        known-good snapshot plus generated test (when available)
logs/**                 raw target/base/control/bisect/suite logs
```

`manifest.json` hashes all evidence payloads. `attestation.json` signs the canonical
statement whose subject is the manifest hash, following the in-toto subject/predicate
shape without claiming SLSA build provenance that the local reference runner does not
possess.
