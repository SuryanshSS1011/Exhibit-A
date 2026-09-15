# Exhibit A continuation plan

**Audit date:** 2026-09-01

**Execution mode:** autonomous, one reviewed feature per commit

**Verification pauses:** after each milestone

**Git policy:** local commits are authorized; do not push

**Primary invariant:** providers and connectors may supply untrusted facts, but only
claim-specific deterministic code may decide a verdict.

This is the implementation contract for work after the original next-generation plan.
It is intentionally excluded from the public documentation site until the corresponding
features exist. Each numbered item should remain independently reviewable and revertible.

## Audited baseline

The original six-phase roadmap is complete. The repository now has:

| Original phase | Evidence in the current tree | Status |
| --- | --- | --- |
| Provider abstraction | `providers/base.py`, strict role config, Codex CLI, Ollama, OpenAI-compatible, and Anthropic adapters | Complete |
| Runtime identity honesty | requested and confirmed model fields plus explicit `unknown_*` reasons in proposal evidence | Complete |
| Verdict and truth separation | `VERIFIED`, `PARTIAL`, `FAILED`, `UNCERTAIN` and separate execution, goal, and release truth | Complete |
| Evidence connectors | typed read-only local-test, Git metadata, and GitHub CI-status connectors with hash-linked provenance | Complete |
| Second claim type | deterministic behavior-preserving refactor judge, collector, EEF v2 archive, replay, and passports | Complete |
| Provider-blind verifier | explicit regression coverage proving provider changes cannot affect deterministic verdicts | Complete |
| Dogfood and passports | a real historical timeout bug plus credential-free JSON and standalone HTML passports | Complete |

The 2026-09-01 closeout gate passed with 397 engine tests, 5 web tests, Ruff lint and
format checks, and the production web build. Exact test counts are deliberately omitted
from user-facing documentation because they drift on every feature commit.

## What remains and why it is ordered this way

The next bottleneck is no longer provider breadth. It is the strength and portability of
the trust artifact. EEF currently uses a shared HMAC key, its replay environment depends
on a mutable base-image tag already present in the local cache, and remote CI facts are a
library-level evidence source rather than part of a complete release-truth workflow.

The sequence below therefore hardens trust and replay first, then proves connector
portability, and only then adds another claim type. Dependency-bump evidence comes before
migration reversibility because it can reuse the current filesystem/Docker harness and
exercise remote advisory receipts without first introducing a database lifecycle.

## Milestone 1 — Turn CI facts into bounded release truth

- [x] **1. Specify a deterministic release-policy contract**
  Source refs: `models/case.py`, `connectors/ci_status.py`, `verdict/refactor_check.py`,
  `docs/CONNECTORS.md`.
  What to build: Add a versioned, provider-neutral policy input and pure evaluator for a
  pinned revision's normalized CI checks. Define explicit handling for success, pending,
  failure, missing required checks, duplicate names, stale observations, and unknown
  backend states. The evaluator may set `release_truth`; it must never rewrite execution
  truth, goal truth, or the claim verdict.
  Acceptance: The same normalized receipt, policy, and explicit evaluation instant always
  produce the same result; unknown or incomplete facts become
  `NOT_ASSESSED`/`UNCERTAIN`, never success; tests prove that a green CI response cannot
  turn failed claim evidence into `VERIFIED`.
  Verify: `cd engine && python3 -m pytest -q tests/test_release_policy.py tests/test_ci_status_connector.py`

- [x] **2. Archive remote connector receipts in EEF**
  Source refs: `connectors/base.py`, `eef.py`, `eef_refactor.py`, `docs/EEF.md`.
  What to build: Define a bounded connector-receipt section for EEF v3 containing the
  normalized payload, source commitment, collection time, freshness semantics, request
  digest, and response digest. Remote evidence must be snapshotted at collection time;
  offline integrity verification must not call the network. Preserve read support for EEF
  v1 and v2.
  Acceptance: Any unsigned mutation, omission, request mismatch, source-origin mismatch,
  or cross-claim receipt substitution fails closed; structurally invalid evidence remains
  invalid even if re-signed. Mutable CI evidence is visibly timestamped; a trusted HMAC
  holder can still author different coherent evidence, and the docs must say so. Historical
  v2 output remains byte-identical and v1/v2 fixtures continue to verify.
  Verify: `cd engine && python3 -m pytest -q tests/test_eef.py tests/test_eef_refactor.py tests/test_connectors.py`

- [x] **3. Add a release-evidence CLI and public projection**
  Source refs: `cli.py`, `passport.py`, `passport_html.py`, `docs/PASSPORT.md`.
  What to build: Add one explicit CLI workflow that collects CI status for an immutable
  SHA, evaluates a named local policy, archives the receipt, and projects the result into
  JSON/HTML passports. Keep tokens environment-named and private; public artifacts expose
  only allowlisted check names/states, freshness, provenance hashes, and derived release
  truth.
  Acceptance: The command refuses branch names, missing credentials, unsafe API bases,
  oversized responses, and policies with no required checks. Public fixtures contain no
  token, local path, raw HTTP body, or credential-bearing URL.
  Verify: focused CLI/passport tests, then inspect both generated passport fixtures and
  run `git diff --check`.

- [x] **4. Dogfood release truth on Exhibit A's own CI**
  Source refs: `.github/workflows/ci.yml`, `examples/dogfood/`, `docs/CONNECTORS.md`.
  What to build: Pin one public Exhibit A commit with known completed checks, capture its
  normalized receipt, and publish deterministic JSON/HTML examples. The checked fixture
  is the reproducibility input; routine tests must not depend on live GitHub availability.
  Acceptance: Regeneration byte-compares with the checked artifacts; the example clearly
  separates “CI checks passed at collection time” from “the code is correct” and from the
  underlying claim verdict.
  Verify: run the generator in `--check` mode, the focused dogfood test, and visually
  inspect desktop and narrow HTML layouts.

**Milestone 1 checkpoint:** review the schema and public wording before committing to EEF
v3 compatibility. Do not begin signature work while receipt semantics are still moving.

## Milestone 2 — Make passports publicly verifiable and replay environments immutable

- [x] **5. Record the signature and key-distribution decision in an ADR**
  Source refs: `docs/EEF.md`, `docs/PASSPORT.md`, the in-toto envelope specification,
  SLSA provenance, and the Sigstore bundle format.
  What to build: Threat-model shared-secret HMAC, offline public-key verification,
  key rotation/revocation, multiple signatures, keyless identity, transparency logs, and
  air-gapped use. Choose a minimal EEF v4 envelope. The default recommendation is a DSSE
  in-toto statement with an offline public-key verifier; Sigstore bundles should be an
  optional distribution profile rather than a mandatory online dependency.
  Acceptance: The ADR states exactly what identity is and is not proven, how a verifier
  obtains trusted key material, the algorithm-agility policy, and how v1/v2/v3 HMAC
  bundles remain readable without being mislabeled as public identity claims.
  Verify: schema examples validate, signature test vectors are checked independently, and
  a skeptical security review signs off before implementation.

  Decision: [`ADR 0001`](./adr/0001-eef-v4-public-key-signatures.md) selects an offline
  DSSE/in-toto Ed25519 profile with separately distributed trust policy, conservative
  revocation, explicit thresholds, downgrade-safe legacy HMAC wording, and optional
  Sigstore keyless sidecars. Checked examples and OpenSSL-backed vectors, independent of
  Exhibit A's future implementation, make the boundary concrete without adding v4 signing
  code in this milestone.

- [x] **6. Implement EEF v4 public-key signatures**
  Source refs: the accepted ADR, `eef.py`, `passport.py`, and existing HMAC tamper tests.
  What to build: Implement signing and verification behind a small cryptographic backend,
  with domain separation and strict key/algorithm parsing. Add public key ID and rotation
  metadata to passports. If a new cryptography dependency is necessary, isolate it as an
  optional signing extra and document its maintenance cost; do not hand-roll Ed25519.
  Acceptance: Verification requires only the artifact and trusted public key material;
  publishing a demo verification key no longer enables forgery; malformed keys,
  signatures, algorithms, and downgrade attempts fail closed; v1/v2/v3 HMAC compatibility
  tests remain green.
  Verify: official algorithm test vectors, round trips, mutation/tamper matrices, cross-
  process verification, and the full engine suite.

  Result: the optional `public-signatures` backend implements strict DSSE/Ed25519 signing,
  externally anchored root/policy verification, v4 bug and refactor archives, passport v3
  JSON/HTML, and additive CLI workflows. Legacy HMAC output and verification remain on
  their explicit path; mixed trust inputs and downgrade attempts fail closed.

- [ ] **7. Replace mutable replay tags with an immutable environment descriptor**
  Source refs: `executor/docker_exec.py`, `eef.py`, `eef_refactor.py`, `docs/EEF.md`.
  What to build: Bind replay to an OCI image digest and platform, record the exact pytest
  artifact/version, and make the verifier compare the resolved local image identity with
  the signed descriptor before execution. Separate intentional digest refreshes from
  evidence replay. Keep execution network-disabled.
  Acceptance: A retagged image cannot satisfy replay; a missing digest yields a clear
  offline prerequisite rather than a network pull; arm64/amd64 mismatches are explicit;
  both bug-flip and refactor EEFs use the same environment contract.
  Verify: fake-Docker argv tests, wrong-digest/platform negative tests, one real offline
  replay, and byte-reproducibility checks for the generated EEF.

  Progress: EEF v4 bug-flip and refactor archives now share a strict signed
  `eef-replay-environment/v1` contract. Verification checks the exact local repository
  digest, OCI platform, pytest version, and pytest artifact label before building;
  build argv pins the platform and disables both network and pulls. A separate
  `lock-replay-environment` command makes digest refresh an explicit local operation.
  Determinism, fake-Docker argv, wrong-identity, and missing-cache tests pass. The real
  offline replay remains unchecked on this workstation because its Docker cache contains
  zero images or build layers; no image was downloaded to manufacture that result.

**Milestone 2 checkpoint:** publish one passport whose integrity, signer, and replay
environment can all be verified without sharing a secret or contacting a model provider.

## Milestone 3 — Prove connector portability before adding claim breadth

- [x] **8. Add a GitLab CI-status adapter against the same normalized contract**
  Source refs: `connectors/ci_status.py`, the GitLab commit-status API, and the Milestone 1
  policy tests.
  What to build: Add a read-only GitLab adapter with the same security properties as the
  GitHub implementation: TLS for remote hosts, numeric-loopback-only HTTP, no redirects or
  ambient proxies, bounded payload/check count, environment-named token, pinned full SHA,
  explicit pagination behavior, and no verdict authority.
  Acceptance: GitHub and GitLab fixtures normalize to identical policy inputs where their
  semantics overlap; provider-specific unknown states remain explicit; SSRF, credential,
  redirect, truncation, and pagination tests fail closed.
  Verify: focused connector/policy tests plus the full engine suite.

  Result: `GitLabCIStatusConnector` reads the commit-status API through the same
  `CIStatus`/receipt/policy contract as GitHub. It validates origin-bound monotonic
  pagination, aggregates raw-page and check limits without truncation, preserves unknown
  provider states as explicit indeterminate values, and is selectable through
  `release-evidence --forge gitlab`. Provider-parity, SSRF, credential, pagination,
  normalized EEF receipt, and end-to-end CLI tests cover the boundary.

- [ ] **9. Add connector contract fixtures and compatibility tests**
  Source refs: all connector modules and EEF receipt schemas.
  What to build: Create backend-neutral golden fixtures for success, pending, failed,
  stale, duplicate, truncated, and unknown CI states. Add schema-compatibility tests that
  prevent adapters from silently changing normalized meaning or public passport fields.
  Acceptance: Adding a third CI provider requires adapter mapping tests, not judge changes;
  a fixture change is a visible schema decision; deterministic verifier tests import no
  provider or HTTP module.
  Verify: connector contract suite, provider-blind import/dependency test, and EEF fixture
  verification.

**Milestone 3 checkpoint:** confirm that two genuinely different remote sources exercise
the interface without provider conditionals leaking into the policy evaluator.

## Milestone 4 — Add one narrow claim type end to end

- [ ] **10. Specify dependency-bump evidence without claiming universal safety**
  Source refs: the current claim schemas, OSV API/schema documentation, and
  `docs/ENVIRONMENT_DATASET.md`.
  What to build: Define `dependency_bump` inputs, truth table, and non-goals. The subject
  must identify one ecosystem/package, exact before/after resolved versions, lockfile
  commitments, trusted test command, and a timestamped advisory snapshot. Use precise
  wording: the claim can establish “the selected contracts still pass and no matching
  known advisory was present in the captured source,” never “the upgrade is safe.”
  Acceptance: Missing resolution, mutable ranges, unsupported ecosystems, stale advisory
  evidence, network failure, or an ambiguous lockfile produce `UNCERTAIN`; a matching
  advisory or deterministic behavior regression produces `FAILED`; no model decides any
  state.
  Verify: review the truth table against fixtures before writing execution code.

- [ ] **11. Build a bounded OSV advisory connector**
  Source refs: `connectors/base.py`, remote connector security tests, and the OSV query API.
  What to build: Query exact package coordinates/version, validate the OSV response,
  normalize vulnerability IDs, aliases, affected ranges, withdrawal state, and modified
  timestamps, then archive the bounded raw-to-normalized receipt. Prefer exact-version
  queries; do not infer package identity from untrusted prose.
  Acceptance: The connector distinguishes “zero known matches” from unavailable or invalid
  data, caps batch and response sizes, rejects redirects and unsafe endpoints, and supports
  checked offline fixtures for every verdict path.
  Verify: focused connector tests with mocked transport and schema fixtures; no live network
  in the default suite.

- [ ] **12. Ship executable dependency-bump EEF, passports, and dogfood**
  Source refs: the dependency-bump spec, refactor runner/EEF as the reference vertical
  slice, and the immutable environment descriptor from Milestone 2.
  What to build: Execute the same trusted suite repeatedly before and after the resolved
  bump, bind each run to connector receipts, run the pure judge, archive both states and
  advisory evidence, project a privacy-reviewed passport, and dogfood one real historical
  bump. Do not generalize a universal claim framework while implementing it.
  Acceptance: All truth-table paths have deterministic fixtures; replay re-derives truth;
  public artifacts omit source/log/secrets; the dogfood example is byte-reproducible; the
  existing bug and refactor claims remain unchanged.
  Verify: focused unit/integration tests, one real offline replay, passport visual review,
  then the full repository gate below.

**Milestone 4 checkpoint:** stop and assess evidence quality before starting migration
reversibility. A third deep claim is more valuable than a fourth shallow one.

## Reordering after the coverage pilots (2026-09-15)

Milestones 1-4 were ordered on the premise that the next bottleneck was the strength of
the trust artifact. Four preregistered coverage pilots have since measured where the
engine actually loses, and the answer reorders what comes next.

Pilot v8 is the first repository-disjoint corpus: its 17 repositories appear in no prior
corpus, so its numbers measure generalization rather than fit. It found 9/30 VERIFIED and
15/30 reaching the deterministic judge. Of the instances that reached the judge, 60%
verified. Every one of the nine verified Cases is internally consistent, and four of them
recovered from a rejected candidate first.

So the judge works. The loss is entirely upstream of it: 15 of 30 instances never reach
the judge, and 10 of those fail to build an environment. That tail decomposes into three
kinds of thing, only some of which are ours.

Reconstructing an arbitrary project's environment from a lockfile in a clean container is
the hard problem of this space, which is why benchmark suites ship curated per-instance
images rather than building them. Detective mode against arbitrary repositories will stay
environment-bound however many of these are fixed. Prosecutor mode does not have that
ceiling: reviewing a pull request inside the repository's own CI inherits a working
environment for free, and the honesty guarantee is unchanged because the judge does not
care where the environment came from.

Milestones 5-7 follow from that. Items 9-12 remain valid and unstarted; they are not
superseded, only reordered behind work the evidence says matters more.

## Division of labor

Some items need a live provider or real compute and some do not. The split is not a
preference, it is a capability boundary, and mixing them wastes the scarce side.

**Provider-backed or compute-heavy work belongs to the Codex session.** Any preregistered
pilot that spends model calls, any 30-instance study run, and any measurement over real
pull requests. A 30-instance study builds 30 environment images at roughly 0.7-2 GB each;
it must not run on a workstation whose Docker allocation is under 8 GB, because memory
exhaustion is then recorded as an environment failure and quietly corrupts the taxonomy
the study depends on. Report the daemon's memory allocation with any study result.

**Everything else belongs to the Claude session.** Engine, CLI and action code; the
deterministic judge and taxonomy; tests and gates; preregistrations; corpus selection; and
independent verification of every published study before it is pushed. Selection needs
only a zero-scope GitHub token, never a model.

Verification is deliberately not assigned to whoever produced the result.

## Milestone 5 — Close the environment failures that are ours, then stop

Time-boxed on purpose. The remaining wins here are worth about two instances in thirty,
and the platform ceilings are not winnable at all. The point of this milestone is to
separate our defects from the ceilings and then stop paying the tax.

- [ ] **13. Evaluate environment markers against the target platform**
  Source refs: `executor/docker_exec.py` uv-lock marker handling, and the v8 checkpoint for
  `mem0ai-mem0-pr-4203`.
  What to build: That instance failed on `No matching distribution found for pywin32==310`,
  a Windows-only package installed into a Linux container. Establish whether the lockfile
  carries a `sys_platform` marker we are dropping, or whether the entry genuinely has none.
  If the marker exists, conjoin it the way dependency-edge markers already are and exclude
  entries that cannot apply to the target platform.
  Acceptance: A package reachable only under a marker that is false for the build platform
  is not installed, and a package with no marker is installed exactly as before. If the
  lockfile carries no marker, record that in the taxonomy as an upstream defect rather than
  inventing one.
  Verify: a lockfile fixture with platform-scoped edges; no network.

- [ ] **14. Name the platform and index ceilings instead of pooling them**
  Source refs: `studies/fix_coverage.py` environment-install taxonomy, and the v8 checkpoints
  for `autogluon-autogluon-pr-5700`, `omnigent-ai-omnigent-pr-23`,
  `microsoft-agent-lightning-pr-573`, and `mlflow-mlflow-pr-20903`.
  What to build: `pinned_distribution_unavailable` currently holds two unrelated things. Two
  instances pin `torch==2.13.0+cpu` and `torch==2.10.0+cpu`, local version identifiers served
  by PyTorch's own index rather than PyPI; two pin distributions with no artifact for the
  build platform at all. Split them so a reader can tell a fixable index gap from an
  architecture ceiling.
  Acceptance: A local version identifier or a lockfile-declared alternate index classifies
  as an index category; a distribution absent for the platform classifies as a platform
  category; neither claims to be the other.
  Non-goal: Do not install from alternate indexes in this item. Honoring a lockfile's index
  under `--require-hashes` extends trust to a third-party host and is its own decision with
  its own threat model, not a taxonomy change.
  Verify: classifier tests over recorded v8 diagnostics.

## Milestone 6 — Make Prosecutor mode reachable

The engine has supported `Mode.PROSECUTOR` and `should_trigger` since before the pilots,
and both are wired to nothing. There is no CLI entry point and no CI integration, so the
mode that does not pay the environment tax is also the mode nobody can run.

- [ ] **15. Give Prosecutor mode a command-line entry point**
  Source refs: `cli.py` `repro` command, `engine.py` Prosecutor branch, and
  `verdict/diff_location.py`.
  What to build: A command that takes a base and head revision of one repository, runs the
  engine in Prosecutor mode against the changed lines, and emits the same Case contract the
  Detective path emits. It must accept an already-built environment rather than constructing
  one, since inheriting the environment is the whole point of the mode.
  Acceptance: A proven flip on a changed line produces `VERIFIED`; a candidate outside the
  diff is refused before execution; a run with no usable environment is honest silence with
  a stated reason, never a constructed environment.
  Verify: end-to-end against the checked-in fixtures with the local executor.

- [ ] **16. Render a review comment that can only speak with proof**
  Source refs: `passport.py` for the credential-free projection discipline, and
  `operations/policy.py`.
  What to build: A renderer from a Case to a pull-request comment containing the failing
  test, the command to reproduce it, and both execution logs. Silence renders nothing at
  all, not a comment reporting that nothing was found.
  Acceptance: A non-VERIFIED Case renders no comment. A rendered comment contains no
  repository-local path, no credential, and no model prose presented as a finding.
  Verify: snapshot tests over sealed Cases, including the negative cases.

- [ ] **17. Ship the action that runs it in a repository's own CI**
  Source refs: `.github/workflows/ci.yml` for the existing job shape, and `should_trigger`.
  What to build: A composite action triggered on `ready_for_review` or an explicit review
  command, running inside the repository's existing environment, posting a comment only on a
  proven flip. Untrusted pull-request content reaches the engine as argv only, and the action
  requests the narrowest token that can comment.
  Acceptance: The trigger policy is enforced in the action, not merely in the library; a
  fork pull request cannot obtain write credentials; a run that proves nothing exits zero
  and comments nothing.
  Verify: exercise the action against this repository before any external one.

## Milestone 7 — Measure Prosecutor precision the way coverage was measured

- [ ] **18. Preregister and run a Prosecutor precision pilot**
  Owner: the Codex session. Needs a live provider and real compute.
  Source refs: `operations/policy.py` `semantic_precision`, `studies/self_audit.py`, and the
  v8 preregistration for the discipline to copy.
  What to build: A preregistered study over real merged pull requests with known outcomes,
  reporting human-confirmed regressions divided by all human-judged flags, with Wilson
  intervals. Silence is the default and is not a failure. Report the count of pull requests
  on which the engine said nothing alongside the precision figure, because a precise
  reviewer that never speaks is not a product either.
  Acceptance: The corpus is repository-disjoint from v5 through v8, selection precedes any
  provider call, and the report records the platform and the daemon's memory allocation.
  Non-goal: Do not compare this figure with the Detective coverage figures. They measure
  different claims on different populations.

## Deferred until the milestones are complete

- Migration reversibility needs an explicit database threat model, engine/version pinning,
  schema and selected-data digests, transactional semantics, and cleanup guarantees. It
  should be its own plan, not an extension hidden inside dependency-bump work.
- Issue trackers and generic HTTP connectors remain deferred until a real claim requires
  them. A generic endpoint without claim semantics would recreate the premature abstraction
  the original plan warned against.
- Automatic controller/merge behavior remains out of scope. Release truth may advise a
  controller, but Exhibit A should not gain write authority as a side effect of evidence
  collection.
- Performance, race, and broad security claims remain outside the current deterministic
  contract unless a narrowly reproducible judge is designed first.

## Required gate for every item

1. Re-read the complete diff against the item's acceptance criteria and the hard verifier
   invariant.
2. Run focused tests for the changed behavior.
3. Run the repository gate:

   ```bash
   cd engine
   python3 -m pytest -q
   ruff check .
   ruff format --check .

   cd ../web
   npm test
   npm run build
   ```

4. Run `git diff --check`, inspect staged files, and commit only that item with an
   imperative one-line subject and no attribution trailer.
5. Do not push. Pause after each milestone for architectural review.

## Research basis

- [SLSA build provenance v1.2](https://slsa.dev/spec/v1.2/build-provenance) treats
  provenance as an attestation binding artifacts to a build definition and resolved
  dependencies; this motivates signed environment and material identities rather than
  narrative replay claims.
- [in-toto's envelope specification](https://github.com/in-toto/attestation/blob/main/spec/v1/envelope.md)
  recommends an authenticated payload type, signatures over the envelope payload, key IDs,
  and verification before parsing the statement.
- [Sigstore's bundle format](https://docs.sigstore.dev/about/bundle/) packages signatures
  with verification material and can carry DSSE attestations; it informs the optional
  public distribution profile without forcing live Sigstore access into offline replay.
- [Docker's digest guidance](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest-immutable-identifier)
  distinguishes mutable tags from immutable digests and supports binding replay to an exact
  image identity.
- [GitLab's commit-status API](https://docs.gitlab.com/api/commits/#commit-status) exposes
  status for a pinned commit and is the second concrete backend needed to test the CI
  normalization boundary.
- [OSV's query API](https://google.github.io/osv.dev/api/) accepts exact package versions or
  commit hashes and returns machine-readable vulnerability records; its timestamped response
  is evidence about known advisories, not proof of universal absence of vulnerabilities.
