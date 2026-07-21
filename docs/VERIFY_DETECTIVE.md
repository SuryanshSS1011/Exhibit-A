# Verify the live Detective path

Use this runbook before recording the demo. It takes about 10 minutes and proves
three different things in order: the local execution pipeline, the deterministic
stage fallback, and a real Codex-generated fail-to-pass Case.

Run every engine command from the repository's `engine/` directory.

## 1. Preconditions

```bash
cd engine
python3 --version
```

Python 3.11 or newer is required. The live generator resolves its Codex executable
in this order:

1. `EXHIBIT_A_CODEX_BIN`, when set;
2. `codex` on `PATH`;
3. `/Applications/ChatGPT.app/Contents/Resources/codex`;
4. `~/.local/bin/codex`.

Print the exact executable Exhibit A will use:

```bash
python3 -c 'from exhibit_a.hypothesis.generator import _resolve_codex_binary; print(_resolve_codex_binary())'
```

If resolution fails, point to an executable explicitly and repeat the check:

```bash
export EXHIBIT_A_CODEX_BIN=/absolute/path/to/codex
```

The CLI must already be authenticated. Check the resolved executable rather than
assuming the shell's `PATH` matches Exhibit A's fallback logic:

```bash
exhibit_codex_bin="$(python3 -c 'from exhibit_a.hypothesis.generator import _resolve_codex_binary; print(_resolve_codex_binary())')"
"$exhibit_codex_bin" login status
```

The expected result is an authenticated login, such as `Logged in using ChatGPT`.
The generator uses `gpt-5.6-sol` by default. Override it only when intentionally
testing another available model:

```bash
export EXHIBIT_A_MODEL=gpt-5.6-sol
```

The Codex subprocess needs write access to its authenticated state under `~/.codex`
and network access to the model. Run this verification in a normal terminal. A
restricted workspace process may resolve the binary correctly but fail with a
read-only `state_5.sqlite` or an app-server `Operation not permitted` error.

## 2. Fast offline sanity

First exercise intake, suite preflight, candidate policy, disposable execution, and
honest silence without invoking a model:

```bash
python3 -m exhibit_a.cli repro ../fixtures/buggy_slice \
  --fixed ../fixtures/fixed_slice \
  --claim "last_n drops the last row" \
  --offline
```

This command intentionally exits `1` with `INSUFFICIENT_EVIDENCE`: the diagnostic
stub proposes a vacuous placeholder test, and the gate rejects it. That is a healthy
offline smoke result, not a failed setup. It should print a Case path and must not
crash or modify either fixture.

Next open both deterministic stage fallbacks:

```bash
python3 -m exhibit_a.cli repro \
  --replay ../fixtures/cases/inventory_proven.json --json

python3 -m exhibit_a.cli repro \
  --replay ../fixtures/cases/inventory_silence.json --json
```

The first exits `0` with raw verdict `PROVEN` and disposition
`PROVEN_REGRESSION`. The second intentionally exits `1` with
`INSUFFICIENT_EVIDENCE` because it has no proven pass state.

## 3. Run the live Codex investigation

This is the hackathon's critical verification. Do not pass `--offline`:

```bash
python3 -m exhibit_a.cli repro ../fixtures/buggy_inventory \
  --fixed ../fixtures/fixed_inventory \
  --claim "stock_for should return zero for an unknown SKU instead of raising KeyError" \
  --expect KeyError \
  --json
```

The command uses the trusted local fixture executor, so Docker is not required. A
healthy run normally completes within a minute, although the Codex subprocess has a
four-minute timeout. It writes the terminal artifact to
`.exhibit-a/cases/<case-id>.json`.

### Pass checklist

- [ ] The existing-suite preflight completes without an infrastructure error. These
      fixtures contain no existing tests, so `no tests ran` is treated as a clean
      suite-gap baseline.
- [ ] Codex emits at least one concrete hypothesis and a repository-relative pytest
      file.
- [ ] The generated command names only that pytest file.
- [ ] The target checkout fails five times.
- [ ] Every target failure has the same `KeyError` signature.
- [ ] The fixed checkout passes the exact same test once.
- [ ] The raw deterministic verdict is `PROVEN`, `deterministic` is `true`, and
      `suite_gap` is `true`.
- [ ] The generated test appears in `fail_to_pass` and the original fixture trees
      remain unchanged.

### Verdict versus disposition

The live Detective CLI currently has no PR/issue intent context and therefore does
not invoke the separate intent judge. Its honest output is:

```text
verdict: PROVEN
intent_judgment: not_assessed
disposition: BEHAVIOR_CHANGE
```

That is a successful live Detective verification: execution proved the behavior
delta, while the UI neutrally asks whether it was intended. Do **not** describe this
live result as `PROVEN_REGRESSION`; that label requires the separate fallible intent
judgment to classify the proven delta as unintended. The sealed proof exhibit carries
grounded issue/docstring intent and demonstrates the `PROVEN_REGRESSION` presentation.

## 4. Verify the live SSE web path

Start the web process from a second terminal. It must inherit the same Codex login
and any `EXHIBIT_A_CODEX_BIN` / `EXHIBIT_A_MODEL` overrides:

```bash
cd web
npm run dev
```

Open [http://localhost:3000](http://localhost:3000), remain on **Detective**, and use:

```text
Reported State: ../fixtures/buggy_inventory
Known Fixed State: ../fixtures/fixed_inventory
Claim: stock_for should return zero for an unknown SKU instead of raising KeyError
```

Click **Investigate**. The Chain of Custody should advance through suite preflight,
generation, hypothesis, five failing target runs, one passing base run, and the
terminal Case. The final Case displays the neutral **Behavior Change** disposition
and the adjacent raw fail/pass evidence.

To verify the API without the browser, run this from another terminal while the dev
server is active:

```bash
curl -sS -N --fail-with-body \
  -X POST http://localhost:3000/api/investigate \
  -H 'content-type: application/json' \
  --data '{"repo":"../fixtures/buggy_inventory","fixed":"../fixtures/fixed_inventory","claim":"stock_for should return zero for an unknown SKU instead of raising KeyError","expect":"KeyError"}'
```

The stream must terminate with `event: verdict` followed by `event: case`. Seeing
only early phase events is not sufficient—repeat once and inspect the server terminal
for an interrupted child process before calling the web path verified.

## 5. Common failures

| Symptom | Meaning and fix |
| --- | --- |
| `Codex CLI not found` | Set `EXHIBIT_A_CODEX_BIN` to the absolute executable path, then rerun the resolver check. |
| `Not logged in` or authentication failure | Run `"$exhibit_codex_bin" login` in a trusted terminal, then confirm with `login status`. |
| Read-only `~/.codex/state_5.sqlite` or `Operation not permitted` | The verification is running inside a restricted filesystem/process sandbox. Run it in a normal terminal with access to the authenticated Codex state. |
| Codex returns no admissible candidate | The result should be `INSUFFICIENT_EVIDENCE` with a concrete generation/rejection reason. This is honest silence, not a crash. Retry once; do not replace it with a claimed proof. |
| Target failures disagree across reruns | The candidate is flaky and must remain quarantined/silent. Use the sealed exhibit for the stage; do not lower the determinism count. |
| Docker daemon unavailable | Omit `--docker` for these dependency-free trusted fixtures. Do not weaken Docker isolation for an untrusted repository. |
| Web UI stops before the terminal Case | Confirm the CLI live command first, inspect the Next terminal, then verify the SSE API emits both `verdict` and `case`. |
| Raw `PROVEN`, disposition `BEHAVIOR_CHANGE` | Expected for live Detective without intent context. The flip succeeded; regression intent was not assessed. |

## 6. Stage fallback

If the live model is slow, unavailable, or produces honest silence on recording day,
use the already-wired **Replay proof** and **Replay silence** buttons under **Sealed
demo exhibits**. They make no model call and execute no test, so they are deterministic.
State clearly that they are recorded Case Files; do not present a replay as a live run.

Last verified locally on 2026-07-21:

- the exact live CLI command produced a deterministic five-fail/one-pass `PROVEN` Case;
- the exact SSE request emitted the full event sequence and terminal Case;
- the live Detective disposition was correctly conservative at `BEHAVIOR_CHANGE`;
- both sealed replay commands returned their documented results.
