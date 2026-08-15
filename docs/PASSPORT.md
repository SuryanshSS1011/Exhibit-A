---
layout: default
title: Public Evidence Passport
---

# Public evidence passport

The public passport is a deterministic, credential-free JSON projection of a verified
Executable Evidence Format bundle. It supports both `bug_flip` and
`behavior_preserving_refactor` claims through one schema:
`exhibit-a-passport/v1`.

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

```bash
python3 -m exhibit_a.cli passport case.eef \
  --signing-key /secure/eef.key --out case.passport.json
```

The HMAC signature proves that the holder of the shared verification key minted the EEF;
it does not establish a public publisher identity. The passport carries the signature but
never the verification key. Its MAC is domain-separated from EEF signatures so one
artifact type cannot be replayed as the other. It also distinguishes signed integrity
verification from fresh execution: `execution_replayed` remains `null` because passport
creation is offline and does not execute archived code.

The private EEF remains the full-fidelity replay artifact. The JSON passport is the small,
reviewable public summary linked back to that archive through `manifest_sha256`.
