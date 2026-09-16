# Pilot v9 frozen corpus

Nineteen merged pull requests across nine repositories, each declaring that it changed no
behaviour, none from any repository used by pilots v5 through v8. Frozen before any probe,
provider call, container build, execution, Case, or verdict.

**This is short of the preregistered target of 30, and it is frozen short on purpose.**

| Selection fact | First frame | After amendment-001 |
|---|---:|---:|
| Repositories screened | 394 | 214 |
| Registered scan limit | 800 | 800 |
| Excluded as prior-corpus members | 46 | 18 |
| Environment-ineligible | 298 | 146 |
| Eligible retained | 50 | 50 |
| Retained repositories with no qualifying pull request | 40 | 30 |
| Included instances | 12 | **19** |
| Repositories represented | 4 | **9** |

## Why it is short

Not the scan limit, which was never approached in either frame, and not the admission
rule, which was not touched. Both runs stopped on `registered_candidate_universe_exhausted`
with the fifty-repository frame full: what ran out was qualifying pull requests.

The first frame ran out for a reason that turned out to be a defect in the frame rather
than a fact about the population. Star-ranked Python repositories are ranked by popularity,
not by whether anything is merged in them; sampling the ones that yielded nothing found
`faif/python-patterns`, `satwikkansal/wtfpython` and `Pythagora-io/gpt-pilot` with 0, 0 and
1 merged pull requests respectively in the entire six-month window. They are reference and
teaching repositories. `amendment-001.json` records the diagnosis and adds
`pushed:>=2026-06-01`, which moved the yield from 12 instances over 4 repositories to 19
over 9.

The second frame ran out for a different and more interesting reason. Thirty of its fifty
eligible repositories still produced no qualifying pull request, and those repositories are
actively developed. **Merged changes that declare themselves behaviour-preserving, touch
production Python, and carry a pinned environment resolvable on both revisions are simply
uncommon.** That is a finding about the population rather than about the frame, and it is
why selection stops here.

## Why there is no second amendment

The first amendment fixed a defect: the frame was selecting for the wrong property. A
second one would be chasing a number, which is what preregistration discipline exists to
prevent. The remaining shortfall has no comparable diagnosis behind it — the population is
thin — so the honest response is to publish the size actually reached and the bound it
supports rather than to keep widening until 30 appears.

## What nineteen can and cannot support

A result of zero false convictions over nineteen instances bounds the true rate at 16.8%
with 95% confidence. The registered thirty would have bounded it at 11.4%; the first
frame's twelve would have allowed 24.2%.

So a clean result here is evidence that the engine is not *frequently* wrong on declared
no-ops. It cannot establish a low rate, and it must not be reported as one.

Nine repositories, at most four instances from any one, is a narrow base. Read the result
as being about these nine codebases and their conventions before reading it as being about
Python.
