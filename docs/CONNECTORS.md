---
layout: default
title: Evidence connectors
---

# Evidence connectors

Connectors are typed, read-only fact collectors. They return raw evidence plus a
tamper-evident provenance receipt; they never decide whether a claim is true. The
deterministic claim judge remains the only component allowed to issue a verdict.

Every receipt records the connector and version, evidence capability, credential-free
source identity and revision, observation time, freshness basis, a plain description,
and a unique evidence ID plus request, response, artifact, and combined SHA-256 digests.
The artifact digest lets a passport reader match a receipt to an emitted or minimized
test without publishing rejected test code, commands, or raw logs a second time. Security
metadata is explicit rather than
inferred: the local development executor reports `host_subprocess`, while the production
Docker executor reports `container`; both operate on a disposable source copy. Docker
reports network and credential access as disabled. The development-only local executor
truthfully reports `host_unrestricted` network and `ambient_host` credentials rather than
claiming process containment it does not provide.

The test adapter wraps candidate and minimization execution. Environment preparation,
suite preflight, mutation testing, and the flip judge remain unchanged. Network-enabled
test requests are rejected at this connector boundary.

The Git metadata adapter is the first non-test evidence source. It reads an already-local
checkout only: no clone, fetch, credential helper, or other network operation. Fixed-argv,
time-bounded Git object commands resolve an immutable commit and record its tree, parents,
author/committer timestamps, and raw changed-path entries without names or email addresses.
Hooks, pagers, global/system config, external diff helpers, prompts, and optional locks are
disabled. Output is hard size-bounded at the subprocess file limit, process groups are
killed on timeout, and untrusted revisions are rejected before Git starts.
Partial/promisor repositories and object alternates are rejected so a metadata read cannot
silently fetch a missing object or cross into another object store. Because v1 has no OS
network sandbox around Git itself, its receipt truthfully reports `host_unrestricted`
network and `ambient_host` credential access despite the connector's local-only command set.
V1 supports standard SHA-1 repositories with UTF-8 paths; other object formats, linked
worktrees, and non-UTF-8 path bytes fail closed rather than being normalized.

The CI status adapters are the first remote evidence sources. The GitHub implementation
reads check runs; the GitLab implementation reads the commit-status endpoint. Each performs
outbound HTTPS GETs only: no write scope, re-run trigger, or artifact download. Plain HTTP
is refused unless the host is a numeric loopback address, so a self-hosted forge can be
pointed at locally without silently downgrading a real one. Ambient proxies and redirects
are disabled, the aggregate response is size-bounded, and the check count is capped.
Oversized input is rejected rather than truncated so a partial read can never look
complete. Credentials come only from a named environment variable, are validated for
control characters, and never reach the payload, receipt, or an error message; a connector
configured without one truthfully reports `credential_access: none`. Because reads happen
in the engine process rather than a subprocess or container, receipts report
`isolation: in_process`.

GitLab project paths are URL-encoded as a single API path component. The adapter requests
the latest status per name with an explicit stable order and 100-item page size, then
follows every `rel=next` link. Each next link must stay on the original origin and endpoint,
retain the exact allowlisted query, and increment the page by one. Empty intermediate pages,
cycles, more than 32 pages, more than 250 statuses, and cross-origin or parameter-injecting
links fail closed. The raw-artifact commitment length-prefixes every page before hashing,
so page boundaries are authenticated. These rules follow GitLab's documented
[commit-status endpoint](https://docs.gitlab.com/api/commits/#commit-status) and
[REST pagination contract](https://docs.gitlab.com/api/rest/#pagination).

The adapters report what the forge said and stop there. They record each run's name,
normalized status/conclusion, and timestamps, and never aggregate those runs into a
"CI passed" claim — that inference belongs to a judge, not a collector. Check runs are
sorted by name so the evidence digest does not depend on the order the forge happened to
return. GitLab's overlapping states map conservatively onto the existing vocabulary:
`pending`/`running` remain incomplete, while success, failure, cancellation, and skip map
to their equivalent completed conclusions. Any new valid GitLab state is retained as an
explicit `gitlab:<state>` incomplete status, which makes release policy `UNCERTAIN` instead
of guessing. Unlike Git metadata, CI status is mutable: a re-run changes it, so receipts
report `point_in_time` freshness and carry the latest completion timestamp observed.

## Deterministic release policy

`release-policy/v1` is the first consumer of normalized CI facts. A policy names one or
more exact, case-sensitive required checks and an evidence age from one second through
31 days. Its pure evaluator receives a normalized status payload, its provenance receipt,
the policy, and an explicit timezone-aware evaluation instant; it performs no I/O and
reads no ambient clock.

No supplied evidence yields `NOT_ASSESSED`. Supplied but partial, stale, future-dated,
digest-mismatched, duplicated, missing, pending, neutral, skipped, or unrecognized evidence
yields `UNCERTAIN`. A fresh required check with a failure, cancellation, timeout, action
requirement, or startup failure yields `UNSAFE`. `SAFE` requires every named check to have
one fresh `completed`/`success` observation. Non-required checks are ignored, so the policy
does not silently expand when a repository adds a new job.

This vocabulary is deliberately bounded: `SAFE` means only that the pinned revision
satisfied the named CI policy at the recorded evaluation instant. It is not a claim of
program correctness. The evaluator returns release truth and a reason but has no access to
the claim verdict, execution truth, or goal truth. A green forge response therefore cannot
admit failed or uncertain claim evidence.

The `release-evidence` command is the end-to-end consumer. It requires an exact remote
`owner/name`, a full lowercase target SHA matching the Case, a strict named policy, and an
explicit evaluation timestamp. The credential argument is an environment-variable name,
never a token. Configuration, path, origin, policy, key, and credential presence are
validated before the single collection call. The resulting Case stores a result-free
policy/receipt/evaluation record beside release truth; offline EEF verification reads the
archived receipt and re-runs the pure policy evaluator, rejecting a changed policy, reason,
receipt, or release label even when the archive has been coherently re-signed.

Hosted API connectors do not inherit process sandboxing from CLI-shaped providers. The
GitHub and GitLab adapters therefore state their narrower posture explicitly: direct in-process HTTPS,
ambient-host credential access, no redirects or proxies, bounded response/check counts,
and read-only source semantics. Future HTTP adapters must supply and document their own
network, credential, redirect, size, and source-origin controls rather than assuming the
Codex CLI subprocess sandbox applies to them.

## Exhibit A CI dogfood

The checked-in [`exhibit_a_ci`](https://github.com/SuryanshSS1011/Exhibit-A/tree/main/examples/dogfood/exhibit_a_ci) example pins
public revision `de669e7e09aa5694911fe524ab30253f75a6b5cc` and the exact normalized receipt
collected for it. GitHub reported five completed successful checks; the local policy names
only `engine` and `web`. The full-fidelity fixture retains all five names so completeness
can be verified, while the public passport exposes the two allowed names plus an omitted
count of three.

The generator never contacts GitHub. It treats the checked receipt as immutable historical
input, evaluates it at its recorded collection instant, and byte-compares the regenerated
JSON and HTML. This keeps routine verification independent of GitHub availability and of
later check reruns. Its `SAFE` value means only “the two required checks satisfied this
policy at collection time”; it neither asserts code correctness nor changes the separately
derived `VERIFIED` timeout-bug verdict.

Test-execution receipts are stored in `Case.evidence_sources` and therefore covered by the
existing EEF hash manifest and signature. The Git adapter returns its typed payload and the
same receipt shape for callers to persist when they opt into that source. Local filesystem
paths and URL credentials are not included in public provenance.

EEF v3 can additionally archive remote CI payloads alongside their receipts. The canonical,
size-bounded section is committed in both the manifest and attestation predicate, and each
entry is bound to the exact claim hash, canonical repository origin and identity, and full
target revision. The verifier recomputes the digest of the normalized request, normalized
response, and their content link without contacting the forge. Connector-specific source
rules bind both adapters to the repository's expected API origin and provider-specific
path; a future adapter must add its own fail-closed rule. The raw response is deliberately omitted;
its artifact digest is a signed commitment to the collected bytes, not a body an offline
reader can independently rehash. V1 and v2 archives remain readable and expose an empty
remote-receipt collection; minting without remote evidence remains byte-compatible v2.

The EEF signature protects these receipts from parties that do not hold the shared HMAC key.
It does not stop a trusted key holder from authoring a different internally coherent receipt,
and it does not independently authenticate GitHub's response. Public-key signer identity and
upstream provenance are separate roadmap decisions.

Connectors are trusted evidence collectors, but they have no verdict authority. Before
raw test output reaches the deterministic judge, the engine validates the connector's
payload type, descriptor-bound metadata, and request/response hash.

Local test receipts are version 2. Their request digest binds the state and revision,
contract artifact, fixed command, timeout, network policy, and prepared environment handle.
A shared fail-closed collector also validates source identity, observation time, descriptor
metadata, primitive outcome fields, and every digest before returning a fresh outcome
snapshot. Both bug-flip and refactor workflows use this same distrust boundary.
