# Model provider boundary

Providers supply untrusted proposals. They never execute or judge evidence, and the
deterministic verifier does not depend on provider selection.

## Runtime model identity

Every response records both the model requested by Exhibit A and the identity confirmed
by the serving backend. A requested name is not proof of what ran. When trustworthy
telemetry is unavailable, adapters must record an explicit reason such as
`unknown_no_telemetry` or `unknown_unverified_backend`; they must not omit the field or
copy the requested name into it. This rule applies equally to hosted APIs, local servers,
routers, and CLI-backed providers.

The provider response currently preserves this identity for its caller. Persisting it in
the Case schema and evidence passport is a separate roadmap step; until that lands, Exhibit
A must not claim that passports contain runtime-model telemetry.

## Transport-specific containment

Provider adapters share a response schema, not an assumption that every transport has the
same security boundary.

- CLI adapters must invoke an argument vector without shell interpolation, bound runtime,
  isolate temporary files, and apply the strongest available read-only and network policy
  to model-initiated tools. The current Codex adapter uses an ephemeral, read-only Codex
  sandbox, passes the prompt on standard input, and does not execute through a shell. Its
  model transport still requires network access, and the child inherits the environment
  needed for Codex authentication; read-only does not mean network-disabled or free of
  ambient read access. That transport access does not authorize generated commands to
  modify the checkout. Future CLI adapters must document and minimize inherited environment,
  readable scope, and transport egress rather than inheriting these properties silently.
- Direct HTTP adapters must send only the intended prompt and schema to the configured
  endpoint, keep credentials out of prompts, responses, and logs, enforce timeouts and
  response-size limits, and never execute returned tool calls. They must also validate
  endpoint and redirect policy, default local endpoints to loopback, use TLS for hosted
  endpoints, and disclose that selected prompt or repository content leaves the machine.
  Any local tool execution added later needs an explicit sandbox equivalent to the CLI
  boundary.

Provider output remains untrusted after either transport. Candidate path validation,
scoped test commands, sandboxed execution, and the deterministic verdict gate remain
downstream requirements.

## Implemented adapters

- `CodexCliProvider`: authenticated Codex CLI with an ephemeral, read-only sandbox.
- `OllamaProvider`: structured chat completions through Ollama's OpenAI-compatible API.
  It defaults to `http://127.0.0.1:11434/v1`, accepts only numeric loopback addresses,
  ignores ambient proxy configuration, disables redirects, bounds response size, applies
  a socket-operation timeout, and never sends or executes tools. It supplies a
  deterministic, size-bounded snapshot of supported Python-repository files without
  following symlinks or reading ignored build, VCS, cache, and environment directories.
  Ollama's response `model` confirms the served tag; the underlying version remains
  `unknown_unverified_backend` because a mutable tag cannot attest to exact weights or
  quantization.
