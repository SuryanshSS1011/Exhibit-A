# Security

Exhibit A executes untrusted code on purpose. Reproducing a bug means running a
repository's own test suite, which runs whatever that repository's `conftest.py` imports.
The containment boundary is therefore the product, not a detail of it.

## Reporting a vulnerability

Report privately through GitHub's [private vulnerability
reporting](https://github.com/SuryanshSS1011/Exhibit-A/security/advisories/new) rather
than opening a public issue. Please include a reproduction; this project's entire premise
is that a runnable reproduction beats a description, and that applies to its own defects.

There is no released version yet and no backport branch: fixes land on `main`.

## Threat model

**Assumed hostile:** repository contents and history, test output, bug-report and claim
text, model output, and anything arriving over HTTP.

**Assumed trusted:** the machine the engine runs on, the operator's shell environment,
signing keys, and any checkout the operator explicitly points at with `--no-sandbox`.

What the boundary is built from:

- Untrusted input reaches `git` and every subprocess as **argv only** — never
  interpolated, never `shell=True`.
- Executors work on a **disposable copy**; source under test is never mutated.
- Container runs are network-off, `--cap-drop ALL`, `no-new-privileges`, read-only
  rootfs, non-root, with pid/memory/cpu limits and a wall-clock budget that kills the
  **container**, not just the client.
- Repo URLs must be HTTPS without credentials, and must resolve to a public address.
- Candidate test paths are validated; the run command is engine-built, never model-supplied.
- Verification fails closed: a bad key, a tampered archive, or a corrupt zip exits
  non-zero and never reports `verified`.
- Public passports are credential-free by construction, asserted negatively in tests.

## Known limitations

These are deliberate, documented, and worth knowing before deploying anything.

- **`--no-sandbox` is host execution.** It exists for trusted in-repo fixtures that ship
  no lockfile. Pointing it at an untrusted checkout runs that checkout's code on your
  machine. That is the flag's stated meaning, not a bug.
- **The web API token is a deployment guard, not user authentication.** The page is
  served the same token so the UI can call the API, so anyone who can load the page can
  call it. It makes an unconfigured deployment inert and turns away callers that never
  loaded the page. Put real authentication in front of the page.
- **The SSRF guard resolves once.** A name is checked against the addresses it resolves
  to at validation time; `git` resolves again when it connects. A DNS rebind between the
  two is not prevented. An egress policy is the real fix for a hostile network.
- **Commit SHAs may be abbreviated at intake** (7–40 hex). The checkout resolves them to
  the full 40 characters before they reach a Case, and refuses a checkout that lands on a
  commit not matching the request, so evidence never carries an abbreviation.
- **The tamper and vacuous-test detectors are heuristics.** They are regex checks over
  candidate source and are bypassable by construction; they exist to catch obvious
  gaming cheaply. The container is the real enforcement, which is why it is the default.
- **Dependency hashes are enforced only when a lockfile ships them.** A version pin says
  which release, not which bytes.

## What is not a vulnerability

- A model proposing a bad test. The judge exists because the model is fallible; a
  rejected proposal is the system working.
- `UNCERTAIN` on a real bug. Silence is the designed answer when nothing cleared the gate.
- Host execution reached through `--no-sandbox`, or through a web deployment whose page
  was left publicly reachable. Both are documented above.
