# Fix-coverage pilot v5

Pilot v5 is a new selection and run, not a rerun of the hash-pinned v4 corpus. It holds the
v4 public repository frame constant while rerunning eligibility with `uv.lock` support, so
the observed corpus change is attributable to the registered environment rule rather than
to star-rank drift.

The method also makes judge reach the primary metric, halts without checkpointing when the
provider reports exhausted quota, and publishes a category-only breakdown of dependency
installation failures. Raw reasons, Cases, generated tests, and logs remain private.

The preregistration was committed before selection, model calls, container builds, test
executions, or v5 verdicts. The frozen corpus and selection report are added only after the
mechanical selector reaches its registered stopping condition.
