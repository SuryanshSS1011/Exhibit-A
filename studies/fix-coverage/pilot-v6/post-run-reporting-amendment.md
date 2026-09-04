# Pilot v6 post-run reporting amendment: separate prior-corpus exclusions

Recorded after all 30 v6 instances completed and before the public report was exported.
The run's Cases, failure categories, environment-install categories, and verdicts are
unchanged.

The first aggregate report treated `prior_corpus_member` as an eligibility exclusion.
That was incorrect: the 30 matching v5 PRs were deliberately removed to satisfy the
pre-execution fresh-corpus amendment, not because they failed v6 eligibility. Leaving them
in the eligibility count would inflate `screened_candidates` from 61 to 91 and depress the
descriptive end-to-end VERIFIED fraction from 2/61 to 2/91.

The reporter now exposes those 30 records separately as `prior_corpus_exclusions`.
`eligibility_exclusions` contains only the 31 candidates rejected by the registered
environment and production-Python rules; `unselected_eligible_candidates` remains the 978
candidates excluded by the repository cap or final sample-size boundary. The primary
30-instance headline, judge-reach denominator, failure taxonomy, and dependency-install
breakdown never depended on this bookkeeping field.

This is a reporting-only correction discovered while checking the completed private
aggregate before publication. Re-running the aggregate reads the already-frozen worker
checkpoints; it does not retry, replace, or reclassify an instance.
