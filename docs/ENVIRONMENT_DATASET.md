# Environment-inference dataset

Exhibit A records each explicit executor `prepare` attempt as private,
`environment-attempt/v1` research data. The recorder is a transparent executor
decorator: it returns the same environment handle and re-raises the same setup error.
It does not make dependency guesses or alter silence decisions.

Each record contains the engine version, repository identity and commit, executor,
bounded repository-shape markers, selected strategy, duration, success/failure, and a
truncated setup diagnostic. It never copies lockfile contents, source, credentials, or
arbitrary repository files. CLI investigations place records next to their Case output
under `environment-attempts/`; study commands use the same private research boundary.

Aggregate empirical recipes with:

```bash
cd engine
python3 -m exhibit_a.cli environment-summary \
  .exhibit-a/environment-attempts \
  --out .exhibit-a/research/environment-summary.json
```

The `environment-summary/v1` output groups attempts by repository shape and strategy
and reports `successes / attempts`. This is an observed rate, not a promise that the
recipe will work on a new repository. Publishing requires a separate privacy review:
repository identities and build diagnostics are private by default.
