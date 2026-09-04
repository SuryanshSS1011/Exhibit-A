# Pilot v5 runtime amendment: resumable provider quota halt

Recorded after 26 of 30 instances had completed and before instance 27 produced an
outcome. The first attempt at instance 27 returned an explicit provider usage-limit
response. As preregistered, the study halted without writing an instance checkpoint, so
the corpus still contains 26 completed outcomes and four unattempted instances.

The first resume revealed that `SubprocessTrialRunner` treated every completed worker
result as reusable, including the deliberately uncheckpointed quota result. It therefore
returned the same quota response without contacting the provider and could never resume.
The existing unit test used an in-memory runner and did not exercise this cache boundary.

The runner now retains quota attempt directories as non-outcome audit records but skips
their result when looking for a reusable completed outcome. A resume creates the next
numbered worker attempt for the same corpus instance. Its
`prior_incomplete_attempts` count includes the retained quota attempts. Any non-quota
worker result remains reusable exactly as before, so no completed outcome is rerun.

This amendment changes no selection rule, corpus row, run parameter, completed Case,
failure category, judge ruling, or verdict. It makes the preregistered quota-resume rule
executable. The reason and code change are committed before the next provider attempt.
