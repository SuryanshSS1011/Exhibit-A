# Counterfactual patch triangulation

Triangulation asks a second, explicitly untrusted question after a Case is PROVEN: can a
small counterpatch make the frozen test pass without breaking the repository's full
suite? The model proposes a unified diff; it never applies or judges it.

```bash
cd engine
python3 -m exhibit_a.cli triangulate case.json /path/to/buggy-checkout \
  --source package/module.py
```

The harness restricts the patch to caller-allowlisted existing Python production files,
rejects traversal, test edits, binary patches, file creation/deletion, and changes over
80 added/removed lines. It copies the target, applies the diff through fixed Git argv
with hooks disabled and patch text on stdin, then uses the executor's disposable copies
again. It revalidates the admitted target failure, requires deterministic passes on the
patched frozen test, and compares the baseline and patched full suites. Docker execution
remains read-only and network-disabled.

`viable_counterpatch` means this exact patch passed those checks. It does not establish
that the patch preserves all intended semantics, is the only repair, or should be merged.
The `counterpatch-triangulation/v1` report retains the diff, rationale, touched files,
line count, and execution outcomes as a candidate `(bug, counterpatch, test)` research
triple. It never changes the Case verdict or `flip_check.py`.
