# Post-outcome taxonomy refinement

This refinement was made after all 30 outcomes were fixed. It changes no corpus row,
execution, Case, verdict, denominator, or headline.

The preregistered classifier placed six empty-hypothesis Cases in
`no_candidate_proposed`. Inspection of their recorded `silence_reason` showed that every
one was an explicit Codex CLI usage-limit error. They were not six independent decisions
by the proposer to return no candidate. The public analysis therefore relabels all six as
`provider_quota_exhausted` and retains the preregistered taxonomy alongside it.

The reusable classifier now distinguishes:

- `no_candidate_proposed`: the provider completed but returned no candidate;
- `provider_quota_exhausted`: generation failed with an explicit usage/credit ceiling;
- `provider_generation_failed`: another provider failure prevented a candidate response.

The raw preregistered report is retained privately with SHA-256
`eed8281f3c631e4cdcaa578d95dbae6bd8066a5c79188d3e202f3e3437a7bce3`.
This refinement makes the constraint ranking more truthful; it does not improve the
observed 1/30 VERIFIED result.
