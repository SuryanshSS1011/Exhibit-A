# CLAUDE.md

Exhibit A is an evidence engine. Its single rule: **a runnable fail-to-pass test, or
silence.** A model proposes; deterministic code decides. Keep that boundary intact.

See [AGENTS.md](./AGENTS.md) for the rules that apply when acting *as* the hypothesis
generator. This file covers working *on* the repo.

## Commands

Both gates must pass before reporting done — CI runs exactly these.

```bash
# Engine (Python 3.11+; CI uses 3.12)
cd engine
pip install -e ".[dev]"
python3 -m pytest -q            # 363 tests, ~2 min
ruff check .
ruff format --check .

# Web (Node 20 in CI)
cd web
npm ci
npm test                        # vitest
npm run build                   # next build
```

## Architecture

Monorepo with a hard boundary between the fallible model and the deterministic judge.
`engine/` holds the Python evidence engine, `web/` a Next.js 15 / React 19 case-file UI,
`fixtures/` tiny buggy/fixed repo pairs for offline runs. See the README's architecture
section for the annotated tree.

The load-bearing pieces:

- `verdict/flip_check.py` — the sole admissibility judge. Pure and deterministic. It
  trusts execution logs over anything a model claims.
- `verdict/refactor_check.py` — the second claim type (behavior-preserving refactors).
  Separate judge; it does not reuse or weaken `flip_check`.
- `models/case.py` — the Case contract, mirrored in TypeScript at `web/src/lib/case.ts`.
  Changing one without the other breaks `test_case_schema_sync.py`.
- `eef.py` / `passport.py` / `passport_html.py` — signed evidence archives and the
  credential-free public projections derived from them.
- `providers/` — model transports. They propose only; they never execute or judge.
- `connectors/` — typed read-only fact collectors that emit provenance receipts.

Scores (mutation, minimization, evidence strength) describe evidence. They never gate it.

## Invariants — don't break these

- Nothing but the deterministic judge issues a verdict. Providers, connectors, and
  studies produce inputs and descriptions, not conclusions.
- Untrusted input (repo URLs, SHAs, claim text, model output) reaches `git` and
  subprocesses as **argv only** — never interpolated, never `shell=True`.
- Executors work on a disposable copy. Source under test is never mutated.
- Public passports are credential-free by construction: no source, no raw logs, no
  repository-local paths. `test_passport.py` and `test_dogfood_passport.py` assert this
  negatively — if you add a field, prove it can't leak.
- Verification fails closed. A bad key, a tampered archive, or a corrupt zip must exit
  non-zero and never report `verified`.

## Gotchas

- **The ruff `select` list is pinned on purpose.** `engine/pyproject.toml` and the root
  `ruff.toml` both declare it explicitly because ruff 0.16 expanded its implicit default
  and turned a clean tree red with 147 findings. Don't "simplify" it away — widen the
  rule set deliberately or not at all.
- **The dogfood passports are byte-reproducible artifacts.** After touching
  `examples/dogfood/timeout_false_verified/generate.py`, re-run it with `--check`. It
  pins pytest `9.1.1` exactly, because pytest's failure renderer is inside the signed
  log. It also needs real local git history for the two commits it archives.
- **New docs pages need two things**: Jekyll front matter (`layout: default` + `title`)
  and an entry in `docs/index.md`. Without both, the file ships but the site never
  renders it. `VERIFY_DETECTIVE.md` is excluded in `_config.yml` by intent.
- `engine/.exhibit-a/` is runtime output from local runs, gitignored. `submission/` and
  `.exhibit-internal/` are gitignored too and must never be committed.
- The engine has no runtime dependencies. Keep it that way; `dev` extras are pytest and
  ruff only.

## Conventions

- Commits: imperative, capitalized subject (`Add secure Git metadata connector`); explain
  the *why* in the body when it isn't obvious. One logical change per commit.
- **No `Co-Authored-By` or AI-attribution trailers.**
- Fixing a bug means adding the test that would have caught it. Verify it fails without
  the fix before you keep it.
- Match the surrounding style. The codebase raises typed errors with specific messages
  (`raise ValueError("EEF ...")`) and catches narrow exception tuples — follow that
  rather than broad `except Exception`.
