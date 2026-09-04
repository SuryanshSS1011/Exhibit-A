# Fix-coverage pilot v1

This directory is the public audit trail for the first corpus-level measurement of
Exhibit A's VERIFIED coverage. The method in `preregistration.json` is committed before
corpus selection or any instance execution. The generated `corpus.json` and public result
report are added only after the run; private Cases, model output, and raw logs remain under
the ignored `.exhibit-a/` tree.

The sequence matters:

1. commit the instrument and preregistration;
2. mechanically select and commit the corpus, without running Exhibit A on an instance;
3. run the resumable pilot exactly once per completed instance;
4. publish the aggregate, exclusions, taxonomy, provenance, and honest interpretation.

Any method change after step 1 receives its own commit explaining why. A low VERIFIED
fraction, an exhausted corpus, or an unavailable cost figure is a valid result.

The original top-50 frame was exhausted with zero included instances. See
[`selection-report.md`](./selection-report.md) and the complete [`corpus.json`](./corpus.json).
This is not reported as 0% VERIFIED because no investigation was run.
The pre-outcome protocol amendment for a second attempt is preserved under
[`../pilot-v2/`](../pilot-v2/).
