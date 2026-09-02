---
layout: default
title: "ADR 0001: EEF v4 public-key signatures"
---

# ADR 0001: EEF v4 public-key signatures

- **Status:** Accepted for implementation
- **Decision date:** 2026-09-02
- **Scope:** EEF v4 and public passport v3 authenticity; EEF v1/v2/v3 compatibility

## Context

EEF v1, v2, and v3 use HMAC-SHA256. A verifier must receive the same secret that can
mint a valid bundle, so publishing a verification key also grants forgery authority.
HMAC remains useful for private integrity checks, but it cannot support a public claim
that a separately trusted publisher signed an artifact.

The replacement must work offline, preserve deterministic verification, make identity
claims no stronger than their trust source, support rotation without an envelope change,
and leave room for keyless signing without making a network service mandatory. It must
also keep signature policy outside model providers and outside deterministic claim judges.

DSSE solves only message framing. It authenticates an exact payload and its type, but it
does not define algorithms, key IDs, identity, trust roots, thresholds, timestamps, or
revocation. Those are therefore explicit parts of this EEF profile rather than properties
we attribute to DSSE.

## Decision

EEF v4 will use a DSSE 1.0.2 JSON envelope containing an in-toto Statement v1. The
mandatory `eef-ed25519-v1` profile uses RFC 8032 Ed25519 and a separately acquired local
trust-root snapshot. Core verification performs no network access. A Sigstore keyless
bundle is an optional additional identity profile, never a fallback and never a source of
trust merely because its verification material is embedded.

The signed statement remains an Exhibit A evidence attestation. It is not SLSA Build
Provenance: it does not describe a conforming build platform, build definition, and
resolved dependency set. We borrow SLSA's separation of cryptographic signer, claimed
publisher, and local authorization policy without adopting its provenance predicate.

The checked examples are:

- [`eef-v4-manifest.json`](./fixtures/eef-v4-manifest.json)
- [`eef-v4-attestation.dsse.json`](./fixtures/eef-v4-attestation.dsse.json)
- [`eef-v4-trust-root.json`](./fixtures/eef-v4-trust-root.json)
- [`eef-v4-trust-anchor.json`](./fixtures/eef-v4-trust-anchor.json)
- [`eef-v4-signature-vector.json`](./fixtures/eef-v4-signature-vector.json)
- [`passport-v3.dsse.json`](./fixtures/passport-v3.dsse.json)
- [`passport-v3-signature-vector.json`](./fixtures/passport-v3-signature-vector.json)

These examples are an implementation contract, not production credentials. Their private
seed is copied from an RFC 8032 test vector and is intentionally public.

## Mandatory envelope profile

`attestation.json` in an EEF v4 archive is a DSSE envelope with:

- `payloadType` exactly `application/vnd.in-toto+json`;
- `payload` equal to Base64 of the exact UTF-8 bytes of one canonical JSON statement;
- one or more `signatures`, each with a `keyid` hint and Base64 `sig`;
- an authenticated statement `_type` of `https://in-toto.io/Statement/v1`;
- `predicateType` exactly `https://exhibit-a.dev/eef/v4`;
- one `manifest.json` subject with the SHA-256 of its exact archive bytes; and
- required predicate values `format`, `signatureProfile`, `publisher.id`, `claimType`,
  and the claim-specific truth fields.

Publishers serialize the statement as UTF-8 JSON with object keys sorted by Unicode code
point, no duplicate keys or insignificant whitespace, no escaped Unicode scalar values,
and no trailing newline. Strings must contain only NFC-normalized Unicode scalar values.
The v4 signed schemas permit `null`, booleans, strings, arrays, objects, and integers in
the interoperable JSON range `-(2^53)+1` through `(2^53)-1`; floating-point values are
forbidden. Quotes, backslashes, and control characters use the shortest JSON escape. This
is the existing Exhibit A canonical JSON profile made explicit, not a claim of RFC 8785
JCS conformance. It makes generated archives reproducible. Verification, however, always
checks the signature over the exact decoded payload bytes; it must never parse and
reserialize before signature verification.

The Ed25519 message is DSSE pre-authentication encoding (PAE):

```text
"DSSEv1" SP LEN(payloadType) SP payloadType SP LEN(payload) SP payload
```

Lengths are unsigned ASCII decimal byte lengths without leading zeros. `payloadType` is
case-sensitive. Ed25519 public keys are the 32 raw RFC 8032 bytes and signatures are the 64
raw bytes. Writers emit standard padded Base64. DSSE readers accept either canonical
standard padded Base64 or canonical URL-safe padded Base64 and strictly decode either to
the identical payload bytes used by PAE. They reject mixed alphabets, noncanonical
padding, whitespace, and trailing data. Trust-root public keys use standard padded Base64
only.

The archive serializes the outer envelope with the same canonical JSON profile plus one
trailing newline. Writers sort signatures by `keyid`; this affects deterministic archive
bytes but not what any individual DSSE signature authenticates.

The stable key ID is:

```text
sha256:<lowercase SHA-256 of RFC 8410 SubjectPublicKeyInfo DER>
```

For Ed25519, that DER is the fixed prefix `302a300506032b6570032100` followed by the
32-byte raw public key. The envelope `keyid` is still only an unauthenticated lookup hint.
The identity displayed to a user comes from the independently trusted record whose key
actually verified the signature, not from the hint.

## Trust root and authorization

The v1 trust-root shape is fixed by the checked example. It contains a globally scoped
`rootId`, version, expiry, keys, and policies with stable `policyId` values. Each policy
binds all of the following:

- signature profile;
- exact payload and predicate types;
- authenticated publisher ID;
- allowed purpose (`eef` or `passport`);
- an explicit set of authorized key IDs; and
- a threshold of distinct authorized keys.

Each key record carries the algorithm, raw public key, lifecycle status, and a human
label. The algorithm is selected from this trusted record, never negotiated from the
envelope. Exactly one policy must match the tuple `(profile, purpose, payloadType,
predicateType, publisherId)`; duplicate selectors or policy IDs fail closed. Thresholds
must be between one and the number of unique authorized keys. Duplicate key IDs, duplicate
public-key bytes under different IDs, or noncanonical IDs are invalid.

A verifier must be provisioned with an external trust-anchor record containing `rootId`,
the pinned SHA-256 of the exact trust-root file bytes, the minimum accepted root version,
and the expected policy ID for each allowed purpose, or must obtain equivalent
authenticated metadata from TUF. The expected digest and policy ID cannot come from the
artifact channel or be computed ad hoc beside the artifact. A managed local trust store
durably records the highest accepted version for each `rootId`; a lower version is a
rollback and fails. The root digest covers every byte, including its serialization and
trailing newline. Expiry is evaluated using an administrator-trusted local UTC clock and
the evaluation time is recorded. A root shipped inside an EEF, passport, or Sigstore
bundle is untrusted input and may be used only as a lookup aid.

Root distribution is deliberately separate from artifact distribution. Acceptable
channels include a package-manager trust store, managed configuration, a pinned digest
communicated out of band, or a TUF repository. The root and its digest arriving together
over the artifact channel is not authenticated. First-use trust without an externally
authenticated anchor is not publisher verification and must be labeled untrusted
inspection.

Verification records the root schema version, root version, root SHA-256, local evaluation
time, verified key IDs, publisher ID, and applied policy. This makes an offline result
auditable against the exact policy snapshot that produced it.

## Verification procedure

An EEF v4 verifier must perform these steps in order:

1. Read a bounded DSSE envelope once; reject malformed JSON, duplicate keys, invalid or
   noncanonical Base64, empty signatures, duplicate or noncanonical signature key IDs, and
   resource-limit violations.
2. Load the independently provisioned trust anchor and root. Select the expected
   `policyId` from trusted caller configuration before reading the payload as JSON. Check
   the exact-byte root digest, `rootId`, schema, persistent monotonic version floor, expiry
   using the trusted clock, unique policy ID, key IDs, algorithms, key lengths, lifecycle
   states, and threshold for that policy.
3. Construct PAE from the exact decoded `payloadType` and payload bytes. Treat required
   `keyid` only as an optimization: a changed or incorrect hint must not exclude another
   authorized key from verification. Count each public key that actually verifies at most
   once.
4. Require at least the policy threshold. Untrusted or invalid signatures do not count;
   malformed and duplicate entries fail closed. No profile or algorithm fallback occurs.
5. Only after the threshold succeeds, parse those same verified payload bytes exactly
   once. Require the Statement, predicate, publisher, purpose, and every signed selector
   field to equal the already selected trusted policy. Then confirm no second policy has
   the same selector tuple.
6. Hash the exact `manifest.json` bytes and compare the sole subject digest, then apply all
   existing archive, claim-schema, receipt-linkage, and deterministic-judge checks.
7. Return the cryptographic result, policy result, and evidence result as separate facts.
   A signature cannot change execution, goal, release, or claim truth.

Unknown statement and predicate fields follow in-toto's forward-compatibility rule and
are ignored only when doing so cannot weaken a denial. Unknown security-critical values,
profiles, algorithms, predicate types, and policies are rejected.

## What identity is and is not proven

With `eef-ed25519-v1`, successful verification proves that:

- the exact statement payload was signed by enough private keys corresponding to the
  independently trusted public keys;
- the installed policy authorized those keys for the authenticated publisher, purpose,
  payload type, and predicate type; and
- the statement binds the exact manifest bytes whose entries the EEF verifier checked.

It does **not** prove a legal or natural-person identity, that a signature was made before
a compromise, that the publisher's machine was secure, that archived observations were
truthful, that code is correct, or that the claim verdict is warranted. The deterministic
EEF checks and optional replay establish the latter evidence properties separately.

A publisher ID is a stable policy identifier, not a name asserted by the artifact. One
publisher may rotate among several keys. One key may act for several publishers only when
each exact relationship appears in trusted policy.

## Rotation, revocation, and multiple signatures

DSSE permits multiple signatures over the same payload, while trusted policy supplies the
threshold. The v4 default is threshold one. During planned rotation, a root lists the old
and new keys as `active`, publishers dual-sign, and a later root removes or marks the old
key `revoked`. Trust-root v1 recognizes only `active` and `revoked`; it has no accepting
`retired` state. Distinct-signature counting is by the public key that verified, not by
attacker-controlled `keyid` text. Invalid and unknown signatures do not count, but other
distinct valid signatures may still satisfy the threshold.

Because baseline DSSE has no trusted signing time, removal or revocation is conservative:
it invalidates all baseline signatures from that key, including historical ones, regardless
of a signed `generatedAt` claim. Verification against an explicitly pinned old root can
report only “trusted under historical root version N,” not current trust. Preserving
pre-compromise validity under current policy requires an independently trusted timestamp
or transparency receipt and an explicit compromise-time rule. An offline verifier cannot
know about changes newer than its installed root. Expired or rolled-back roots therefore
yield no verified publisher identity rather than silently accepting stale status.

A separate, explicitly requested historical-audit mode may use a separately authenticated
historical anchor that pins the old root ID, digest, and version. This mode never emits
current publisher identity, never changes or weakens the normal durable version floor, and
can never be selected by artifact contents. Its result is limited to “signature valid
under historical root version N,” alongside the warning that the snapshot cannot know
later revocation.

Thresholds greater than one are supported by the policy shape but are not the v4 default.
Changing signer quorum requires a new trusted root, not an envelope field. Removing or
reordering DSSE signatures never changes the validity of remaining signatures, so a
threshold must always be enforced from trusted policy.

## Algorithm agility

EEF v4 has one mandatory algorithm profile: `eef-ed25519-v1`. It does not accept generic
algorithm names, algorithm values in untrusted artifacts, or downgrade negotiation.
Adding an algorithm requires a new named profile, normative key/signature encodings,
independent vectors, an explicit trust-policy opt-in, and a security review. Verifiers use
an allowlist and reject unknown profiles. Ed25519ph, Ed25519ctx, RSA, and ECDSA are not
aliases for this profile.

This narrow profile is intentional: DSSE already provides domain separation, Ed25519 has
fixed-size deterministic signatures, and a new algorithm can be introduced without
changing the envelope.

## Legacy compatibility and downgrade resistance

EEF v1/v2/v3 readers remain available under their current HMAC code path. Their successful
result is reported as **shared-key integrity verified**, never public publisher identity.
The legacy path requires the shared secret and exact legacy format/predicate pair.

EEF v4 requires a public-key trust root and DSSE. A v4 verifier never treats
`--signing-key` as a public key, never falls back to HMAC after a DSSE or public-key error,
and never accepts an unsigned envelope. A legacy verifier invocation must be explicit;
format sniffing cannot silently select a weaker trust mode. Passports carry the source EEF
format and verification meaning so old HMAC artifacts cannot be relabeled as v4 identity.

## Public passport v3

Passport v3 will use a separate DSSE envelope with payload type
`application/vnd.exhibit-a.passport.v3+json` and profile `passport-ed25519-v1`. Its payload
is the canonical sanitized passport JSON. Required signed fields include its schema and
signature profile, `passportIssuer.id`, source EEF format and manifest SHA-256, source EEF
publisher and verified key IDs, and the root ID/version/SHA-256 used to verify that EEF. A
separate trust policy binds the passport issuer, purpose `passport`, payload type,
authorized keys, and threshold. Passport issuer and EEF publisher are always displayed as
separate identities, even when their identifier strings happen to match. In a standalone
passport, the source publisher, verified EEF key IDs, and source trust-root data are signed
claims made by the passport issuer; they become independently verified EEF facts only when
the source EEF and its externally anchored root are also supplied and verified. The HTML
renderer must preserve that distinction. This payload-type split prevents an EEF statement
signature from being replayed as a passport signature while reusing the same well-reviewed
framing and public-key backend.

Passport v1/v2 HMAC verification remains readable and retains its current limited
meaning. The HTML renderer may render a v3 passport only after verifying its DSSE envelope,
trusted publisher policy, and payload schema.

## Optional Sigstore keyless profile

Sigstore is an additive distribution and identity profile, not a prerequisite for core
verification. A publisher may place a `<artifact>.sigstore.json` Bundle v0.3 beside the
EEF. For an EEF attestation bundle, the Sigstore DSSE payload bytes must be byte-for-byte
identical to the inner EEF statement payload. The sidecar stays outside the archive to
avoid recursive archive hashing. Multiple keyless signers use multiple named sidecars
because the current Sigstore DSSE bundle profile carries one signature. Passport Sigstore
sidecars are outside v1 of this optional profile rather than having an implicit binding.

Accepting `sigstore-keyless-v1` requires a separately configured policy and all of:

- the exact Bundle v0.3 media type and expected DSSE content;
- an exact binding among the DSSE signature, leaf certificate/public key, payload, Rekor
  entry/body, inclusion proof and checkpoint, and certificate-transparency SCT/proof;
- a Fulcio chain to an independently trusted root plus certificate-transparency proof;
- an exact allowed OIDC issuer and subject/SAN pair (issuer-only or SAN-only matching is
  insufficient), with workflow-specific claims when policy requires them;
- Rekor log identity and the proof rules for the exact supported Rekor generation; v1
  inclusion promises and v2 checkpoint/inclusion/TSA semantics are never interchanged;
- an authenticated observation/signing time that places the signature inside the Fulcio
  certificate validity interval; historical Rekor v2 validation requires its applicable
  trusted timestamp evidence; and
- a pinned, fresh Sigstore trusted-root snapshot obtained through TUF or an equivalent
  authenticated channel.

Embedded certificates, roots, checkpoints, and inclusion material are evidence, not trust
anchors. Rekor `integratedTime` is not trusted without its required authenticated proof.
Transparency establishes discoverability and timing properties, not evidence correctness
or publisher authorization. Verification may disclose repository/workflow identity in a
public log, so publishers must opt in knowingly.

Baseline public-key verification and optional Sigstore verification are reported as
separate facts. If policy requests `sigstore-keyless-v1`, an absent, stripped, malformed,
or invalid sidecar fails that requested identity check; the verifier never silently falls
back to the baseline result. A caller that requested only the baseline profile is not made
to depend on an unsolicited sidecar.

Air-gapped Sigstore verification imports the bundle, authenticated trusted-root snapshot,
and its external anchor before disconnecting, makes zero network requests, records that
root's digest and version, and yields no verified Sigstore identity when the snapshot is
expired or rolled back. It cannot discover later revocations while offline.

## Consequences and implementation boundary

The design lets anyone verify with public material without acquiring forgery capability,
keeps offline operation first-class, and supports rotation or quorum through policy. It
also makes trust-root distribution and freshness an explicit operator responsibility.
Keyless verification can offer stronger externally anchored identity and timing, but it
adds PKI, transparency-log, TUF, privacy, and availability complexity; keeping it optional
prevents that complexity from weakening the baseline.

This ADR does not implement v4. The implementation milestone must use a reviewed
cryptographic library behind a small backend; it must not implement Ed25519 arithmetic.
It must add parser limits, official RFC 8032 vectors, the checked EEF-specific PAE vector,
cross-process tests, threshold/rotation/revocation tests, downgrade tests, and the existing
HMAC compatibility suite before v4 becomes writable by default.

## Validation and references

`engine/tests/test_signature_adr.py` validates the checked contract fixtures at a pinned
evaluation time and uses the system OpenSSL implementation to sign and verify both
domain-separated vectors independently of future Exhibit A signing code. OpenSSL with
Ed25519 `pkeyutl` support is therefore a test prerequisite for this ADR and its future
implementation.

Normative and design sources:

- [DSSE protocol 1.0.2](https://github.com/secure-systems-lab/dsse/blob/v1.0.2/protocol.md)
  and [envelope schema](https://github.com/secure-systems-lab/dsse/blob/v1.0.2/envelope.proto)
- [in-toto Attestation Framework envelope 1.2](https://github.com/in-toto/attestation/blob/v1.2.0/spec/v1/envelope.md),
  [Statement](https://github.com/in-toto/attestation/blob/v1.2.0/spec/v1/statement.md),
  and [Resource Descriptor](https://github.com/in-toto/attestation/blob/v1.2.0/spec/v1/resource_descriptor.md)
- [SLSA 1.2 artifact verification](https://slsa.dev/spec/v1.2/verifying-artifacts) and
  [Build Provenance](https://slsa.dev/spec/v1.2/build-provenance)
- [RFC 8032 Ed25519](https://www.rfc-editor.org/rfc/rfc8032.html) and
  [RFC 8410 key encoding](https://www.rfc-editor.org/rfc/rfc8410.html)
- [Sigstore Bundle v0.3](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_bundle.proto),
  [client verification](https://github.com/sigstore/architecture-docs/blob/main/client-spec.md),
  [Fulcio identity](https://github.com/sigstore/architecture-docs/blob/main/fulcio-spec.md),
  and [Rekor v2](https://github.com/sigstore/architecture-docs/blob/main/rekor-v2-spec.md)
- [Sigstore trust-root format](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_trustroot.proto)
  and [TUF root distribution](https://github.com/sigstore/root-signing)
