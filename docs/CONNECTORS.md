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

The first adapter wraps candidate test execution only. Environment preparation, suite
preflight, minimization, mutation testing, and the flip judge are intentionally unchanged.
This proves the interface against an existing evidence source before a second connector
is added. Network-enabled test requests are rejected at this connector boundary.

Connector receipts are stored in `Case.evidence_sources` and therefore covered by the
existing EEF hash manifest and signature. Local filesystem paths and URL credentials are
not included in public provenance.

Connectors are trusted evidence collectors, but they have no verdict authority. Before
raw test output reaches the deterministic judge, the engine validates the connector's
payload type, descriptor-bound metadata, and request/response hash.
