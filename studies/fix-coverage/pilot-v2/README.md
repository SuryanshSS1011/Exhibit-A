# Fix-coverage pilot v2

This is the committed, pre-outcome amendment needed after the original top-50 frame
produced zero runnable instances. Pilot v1 remains intact. V2 changes only how far the
star-ranked repository list is scanned: it seeks the first 50 repositories accepted by
the existing pinned-environment loader, with a hard cap of 1,000 repositories.

The PR rule, post-cutoff date window, verbatim claims, instance checks, ordering, sample
size, model, sandbox, execution budgets, retry policy, failure taxonomy, and analysis are
unchanged. At the time of this preregistration, no Exhibit A verdict or model output had
been observed.

V2 produced only five eligible instances, all from one repository, and therefore was not
executed as the 30-instance pilot. See [`selection-report.md`](./selection-report.md) and
[`corpus.json`](./corpus.json). No model or verdict outcome informed the next amendment.
