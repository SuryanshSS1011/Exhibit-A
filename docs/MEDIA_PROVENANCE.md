---
layout: default
title: Media provenance
---

# Media provenance

Every image and recording in `docs/assets/` is output from the current Exhibit A code at
source commit `30083d0742bbdd08520e8fa3fa3ddb4f75d21acd`. Nothing was mocked,
retyped, composited, color-corrected, or cleaned up. Terminal and browser chrome were
excluded at capture time so usernames,
hostnames, tokens, and home paths never entered a frame. Temporary paths are under
`/tmp/ea`.

The published assets total about 1.2 MiB. Each recording is below 5 MiB and the combined
media is well below the 30 MiB repository budget. SHA-256 values below cover the exact
checked-in bytes.

## Real provider investigation

- **Asset:** `assets/investigation-real-provider.svg`
- **Format and size:** animated SVG, 1160 × 754.72, 46,696 bytes, 44.71 seconds
- **SHA-256:** `3f009e80d7686e79ec431897cfa2c545625deaf7ec854a925443d4f459cbcce8`
- **Produced by:** Asciinema 2.4.0, rendered with `svg-term-cli` 2.1.1
- **Code and inputs:** commit `30083d0`; byte-for-byte temporary copies of
  `fixtures/buggy_inventory` and `fixtures/fixed_inventory`
- **Outcome:** provider `openai-codex-cli`, requested model `gpt-5.6-sol`, confirmed
  model/version `unknown_no_telemetry`; one candidate; five target failures; one base
  pass; eight minimization attempts, two accepted; `VERIFIED`

The recorded command is:

```bash
/opt/homebrew/bin/python3 -m exhibit_a.cli repro /tmp/ea/t \
  --fixed /tmp/ea/b \
  --claim 'stock_for should return 0 for a missing SKU, not KeyError' \
  --expect KeyError --out /tmp/ea/c --no-sandbox --events
```

One provider idle gap was capped at 15 seconds by Asciinema's playback setting. The cast
was trimmed once at the tail, immediately after the real verdict event, to omit the large
final Case JSON and shell prompt. No process output before the verdict was removed, and no
events were altered, reordered, or spliced. The first candidate passed, so there was no
rejection or retry in this run.

## Public passports

### Timeout-verdict passport

- **Asset:** `assets/passport-timeout-verdict.png`
- **Format and size:** PNG, 1600 × 1700, 2×, RGB, 175,064 bytes
- **SHA-256:** `6e1c9d258c307dfeb06cfefe2111bf5de1b7cbcfee358c4f3c1d7fa13307b5e7`
- **Produced by:** system Chrome through Playwright, light theme, direct local-file render
- **Source:** `examples/dogfood/timeout_false_verified/timeout_false_verified.passport.html`

### CI release-truth passport

- **Asset:** `assets/passport-ci-release-truth.png`
- **Format and size:** PNG, 1600 × 1700, 2×, RGB, 178,670 bytes
- **SHA-256:** `dd413569c26698d0b7a80107a2406530902c0a90d8cd09c00c24d0a211a05f08`
- **Produced by:** system Chrome through Playwright, light theme, direct local-file render
- **Source:** `examples/dogfood/exhibit_a_ci/exhibit_a_ci.passport.html`

Both pages were opened directly from the checked-in HTML at commit `30083d0`. The PNGs are
browser screenshots of those pages, not reconstructed passport data.

## Verdict pair

- **Asset:** `assets/verdict-pair.png`
- **Format and size:** PNG, 1600 × 311, 2×, RGB, 238,323 bytes
- **SHA-256:** `bab9502b98e74769b0b019116732a252905e7b0b276d72c8b09b807dc2d54621`
- **Produced by:** macOS Terminal in its light theme; two real commands in adjacent windows;
  native 2× screenshot, then cropped to the content
- **Edited after capture:** cropped from 1600 × 1140 to 1600 × 311 and flattened from RGBA
  to RGB. The original capture was 78% empty terminal below the output. Only blank space
  was removed; no pixel of output was altered, and the file shrank from 1,030,647 to
  238,323 bytes
- **Inputs:** byte-for-byte copies of the two checked-in Cases under `/tmp/ea`

The commands shown are:

```bash
/opt/homebrew/bin/python3 -m exhibit_a.cli repro --replay /tmp/ea/inventory_proven.json
/opt/homebrew/bin/python3 -m exhibit_a.cli repro --replay /tmp/ea/inventory_silence.json
```

The first prints `VERIFIED`; the second prints `UNCERTAIN`. Neither invokes a model or
executes repository code. The two independent windows were positioned side by side before
one screenshot was taken; their contents were not composited.

## Web investigation stream

- **Asset:** `assets/web-investigation-stream.mp4`
- **Format and size:** MP4/H.264 High, yuv420p, 1280 × 720, 30 fps, 59.93 seconds,
  no audio stream, 442,007 bytes
- **SHA-256:** `938375fb4753aaa0770dd3b4f4f2bf07576f1adf1fcd533a089fd53e4960033b`
- **Produced by:** current Next.js UI in system Chrome through Playwright; H.264 encoding
  with FFmpeg 7.1, CRF 28
- **Input:** claim `stock_for should return one for an unknown SKU instead of zero`; both
  reported and fixed paths `fixtures/sandbox_smoke`
- **Outcome:** five target failures and a fixed-state failure; the deterministic judge
  rejects the candidate as fail-to-fail; no refinement is returned; final `UNCERTAIN`

The app was launched from `web/` with this equivalent, privacy-safe command; the local
token value is intentionally not published and never appeared in a frame:

```bash
EXHIBIT_A_API_TOKEN=<ephemeral-local-token> \
EXHIBIT_A_LOCAL_ROOT="$(cd .. && pwd)" \
EXHIBIT_A_PYTHON=/opt/homebrew/bin/python3 \
EXHIBIT_A_MODEL=gpt-5.6-luna \
npm run dev -- --hostname 127.0.0.1
```

The browser submitted the displayed inputs at `http://127.0.0.1:3000/`. The full run took
108.5 seconds. The published video is one continuous final segment: the initial 48.5
seconds of provider wait were trimmed, with no internal cuts, splice, reordered frames,
narration, or audio. This is honest silence, not a failed recording. It also demonstrates
that a retry is conditional: the provider declined refinement, so no retry occurred.

## Finished web case file

- **Asset:** `assets/web-case-file.png`
- **Format and size:** PNG, 1600 × 1400, 2×, RGB, 172,274 bytes
- **SHA-256:** `029dd0f14d9e917a42c6875c3f94b908b0cc81a6d7b198eaf9c2b18579135af7`
- **Produced by:** current Next.js UI in system Chrome through Playwright
- **Input:** the UI's **Replay proof** action at `http://127.0.0.1:3000/`, which streams
  the checked-in `fixtures/cases/inventory_proven.json` sealed Case
- **Outcome:** completed case file with a `PROVEN REGRESSION` stamp, fail/pass comparison,
  generated test, and evidence-strength summary

This still is deliberately a sealed replay, not a frame extracted from the live provider
video. It shows the finished artifact clearly without implying fresh execution.

## EEF tamper refusal

- **Asset:** `assets/eef-tamper-refusal.svg`
- **Format and size:** animated SVG, 1120 × 711.3, 35,670 bytes, 44.99 seconds
- **SHA-256:** `3687004ebb8946460220654a93a02b34848601f4d8c4859414cfc9fc993a16df`
- **Produced by:** Asciinema 2.4.0, rendered with `svg-term-cli` 2.1.1
- **Input:** a real EEF bundled from `inventory_proven.json` and the two inventory fixture
  trees with an ephemeral 32-byte key
- **Outcome:** original verifies and exits `0`; one byte is changed in a copy; verifying
  the copy reports `Bad CRC-32 for file 'attestation.json'` and exits `1`

The commands visible in the recording are:

```bash
/opt/homebrew/bin/python3 -m exhibit_a.cli verify /tmp/ea/case.eef \
  --signing-key /tmp/ea/eef.key
printf 'exit: %s\n' $?
cp /tmp/ea/case.eef /tmp/ea/case-tampered.eef
/opt/homebrew/bin/python3 -c 'from pathlib import Path; p=Path("/tmp/ea/case-tampered.eef"); b=bytearray(p.read_bytes()); i=b.index(b"VERIFIED"); b[i]^=1; p.write_bytes(b)'
/opt/homebrew/bin/python3 -m exhibit_a.cli verify /tmp/ea/case-tampered.eef \
  --signing-key /tmp/ea/eef.key
printf 'exit: %s\n' $?
```

The copy step protects the original archive. The cast is one continuous run with no trim,
splice, reordered command, or altered output.
