# exhibit-a (engine)

The Python Evidence Engine — an engine that only reports a bug with a runnable
fail-to-pass test that fails on the broken code and passes on the fix, or stays silent.

```bash
python3 -m pytest -q
python3 -m exhibit_a.cli repro ../fixtures/buggy_slice --claim "..." [--docker] [--json]
```

Layers: `models/` (the `Case` contract) · `executor/` (swappable sandbox) ·
`hypothesis/` (the Codex seam) · `verdict/` (deterministic flip check) ·
`engine.py` (orchestrator) · `cli.py`.
