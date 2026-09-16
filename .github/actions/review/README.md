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
jobs:
  comment:
    # workflow_run runs with write access and secrets even though the workflow that
    # triggered it had neither, so everything it touches is untrusted input.
    if: github.event.workflow_run.conclusion == 'success' &&
        github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      # Download by run id from the exact triggering run. A name alone can be satisfied
      # by an artifact from a different run.
      - uses: actions/download-artifact@v4
        with:
          name: exhibit-a-comment
          run-id: ${{ github.event.workflow_run.id }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
      # Post as data, never as a shell argument or an expression. Bound to the pull
      # request the triggering run actually belongs to, not one named in the artifact.
      - uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const body = fs.readFileSync('comment.md', 'utf8');
            if (body.length > 65000) { core.setFailed('comment too large'); return; }
            const prs = context.payload.workflow_run.pull_requests;
            if (prs.length !== 1) { core.setFailed('no single originating pull request'); return; }
            await github.rest.issues.createComment({
              ...context.repo, issue_number: prs[0].number, body,
            });
```

Pin every action to a commit SHA rather than a tag in a job that holds write access.

## What this pattern does not give you

Stage one checks out the contribution, including its copy of `.github/actions/review`.
A contribution can therefore replace the action and write whatever it likes into
`exhibit-a-comment.md`. Posting that as comment *text* is not repository code execution,
which is why the split still matters, but a bot-authored comment is not by itself proof
that Exhibit A's judge produced it.

Treat a posted comment as a claim to be checked by re-running the test it contains, which
is the only thing this project ever asks anyone to trust. If you need the comment itself
to be authenticated, stage one must run a trusted checkout of the action rather than the
contribution's copy, and that is not yet built.

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
