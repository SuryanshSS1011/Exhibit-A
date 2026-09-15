# Exhibit A review action

Runs a Prosecutor-mode review inside the environment your own CI job has already built,
and produces a comment only when a test has been found that fails on the change and
passes without it. It posts nothing itself.

## Why it does not post

Reviewing a pull request means executing the contribution's code. On a fork pull request
that code is untrusted, so the job that runs it must not hold credentials that could
write to your repository. `pull_request_target` grants exactly those credentials and is
the wrong trigger here however convenient it looks.

The action therefore ends at a rendered comment on disk and leaves posting to a workflow
that never executes the contribution:

```yaml
# 1. Runs the contribution's code. No secrets, read-only token.
name: review
on:
  pull_request:
    types: [ready_for_review]
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: pip install -e ".[dev]"      # the environment the review will use
      - uses: ./.github/actions/review
        id: review
        with:
          base-ref: origin/${{ github.base_ref }}
          claim: ${{ github.event.pull_request.title }}
      - uses: actions/upload-artifact@v4
        if: steps.review.outputs.comment-path != ''
        with:
          name: exhibit-a-comment
          path: ${{ steps.review.outputs.comment-path }}
```

```yaml
# 2. Posts it. Never checks out or runs the contribution.
name: review-comment
on:
  workflow_run:
    workflows: [review]
    types: [completed]
permissions:
  pull-requests: write
```

For a repository that accepts no fork contributions the two stages can be one, but the
split is the default because the failure mode of getting it wrong is handing a stranger
write access to your repository.

## Inputs

| Input | Meaning |
|---|---|
| `base-ref` | Revision the pull request merges into; must already be fetched, so `fetch-depth: 0`. |
| `head-ref` | Revision under review. Defaults to the checked-out `HEAD`. |
| `claim` | What the change is meant to do. The pull request title is the usual value. |
| `provider-config` | Strict provider configuration JSON. Omitted means the stub proposer, which proves nothing and is useful only for checking the wiring. |
| `image` | A prebuilt container to review in. Omitted means this runner, which is the point: the job has already installed the dependencies. |

## What it does not do

It never builds an environment. Detective mode against an arbitrary repository spends
most of its budget doing that, and pilot v8 lost ten of thirty instances to it. A pull
request is reviewed where its dependencies already are.

It never comments to say it found nothing. Silence leaves no file and no comment.
