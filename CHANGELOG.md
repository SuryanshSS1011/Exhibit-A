# Changelog

Notable changes to Exhibit A. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), and while the
major version is 0 the public surface may still move between minor releases.

## [0.1.0] — 2026-09-03

A hardening pass over the execution boundary and the reproducibility of what the engine
signs. No change to what the judge admits: the flip check and the refactor judge are
untouched, and every verdict this release produces is the verdict `0.0.1` would have.

### Breaking

- **Runs are sandboxed by default.** `--docker` still parses but no longer decides
  anything; host execution now requires `--no-sandbox`, which is only correct for a
  checkout you trust. Repositories without a lockfile — including the in-repo fixtures —
  need that flag, since the pinned-image path refuses to guess dependencies.
- **The web API refuses every request until `EXHIBIT_A_API_TOKEN` is set**, and no longer
  accepts local repository paths unless `EXHIBIT_A_LOCAL_ROOT` names a directory to read
  them from. The `docker` field is gone from the request body.

### Security

- Repository URLs must resolve to a public address; literal and resolved private,
  loopback, and link-local ranges are refused, including IPv4-mapped forms.
- Git commands carry a wall-clock budget; previously a wedged remote could hang a run
  indefinitely.
- A timed-out run is stopped rather than abandoned: containers are named and force
  removed, and host runs are killed by process group. Previously the timeout killed the
  Docker client while the container kept running, and left anything pytest had spawned.
- The web API bounds concurrent investigations and per-run wall-clock time.
- Added `SECURITY.md`, including the limitations this design does not cover.

### Reproducibility

- The sandbox base image is resolved to an immutable digest and folded into the
  environment cache key. A moved tag previously changed what a rebuilt image contained
  while the key stayed put.
- Dependency hashes are enforced for any lockfile that ships them.
- The replay pytest version, and the engine version stamped into research records, each
  have one source of truth instead of four.
- Abbreviated commit SHAs are resolved to the full 40 characters before reaching a Case.

### Added

- Operational logging on stderr, with per-run identifiers, optional JSON output, and
  credential scrubbing. Stdout stays reserved for the `--json` and `--events` protocol.
- Bounded retry for provider HTTP calls on the statuses a server can recover from.

### Testing

- Engine suite 551 → 605 tests, plus 5 opt-in tests that exercise a real Docker daemon;
  web suite 5 → 17.
- CI runs the engine on Python 3.11, 3.12, and 3.13, adds a sandbox job for the real
  executor, and adds ESLint and typechecking for the web app.

## [0.0.1]

Initial working system: the deterministic flip check, the behavior-preservation judge,
signed evidence archives through EEF v4, credential-free public passports, typed
read-only connectors, and the research instrumentation layer.
