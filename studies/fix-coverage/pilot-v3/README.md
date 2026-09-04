# Fix-coverage pilot v3

This final pre-outcome amendment keeps the v2 repository frame and every execution rule,
but recognizes real fixes mechanically through either an exact PR `bug` label or a
case-insensitive title prefix of `fix`, `fixed`, `fixes`, or `fixing`. GitHub results are
bounded to the first 100 merged PRs per repository in the fixed date window.

The v1 and v2 selection failures remain checked in. No Exhibit A outcome or model call was
observed before this rule was committed.

V3 selected 16 instances before a transient network outage caused 50 candidate-detail
requests to fail. The shortfall and all exclusions are preserved in
[`selection-report.md`](./selection-report.md) and [`corpus.json`](./corpus.json); no
included instance was executed.
The transport-only amendment is preserved under [`../pilot-v4/`](../pilot-v4/).
