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

Test-execution receipts are stored in `Case.evidence_sources` and therefore covered by the
existing EEF hash manifest and signature. The Git adapter returns its typed payload and the
same receipt shape for callers to persist when they opt into that source. Local filesystem
paths and URL credentials are not included in public provenance.

Connectors are trusted evidence collectors, but they have no verdict authority. Before
raw test output reaches the deterministic judge, the engine validates the connector's
payload type, descriptor-bound metadata, and request/response hash.
