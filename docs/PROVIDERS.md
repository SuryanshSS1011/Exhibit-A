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

Each provider response is appended to the Case's `proposal_runs` evidence trail. It records
the operation, provider, requested model, explicitly confirmed-or-unknown runtime identity,
token usage, cost and latency when available, the count of untrusted returned tool calls,
and a SHA-256 digest of the structured response that produced the proposal. Responses are
consumed exactly once so refinement records cannot inherit stale proposal telemetry.

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
  endpoint, keep credentials out of prompts, responses, and logs, enforce response-size
  limits, and never execute returned tool calls. They must also validate
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
- `OpenAICompatibleProvider`: the portable Chat Completions subset used by vLLM,
  LM Studio, hosted gateways, and similar endpoints. Remote URLs require HTTPS; plain
  HTTP is accepted only for numeric loopback addresses. The adapter disables redirects
  and ambient proxy discovery, hard-bounds request, repository-context, traversal, and
  response sizes, sends no tools, and never executes returned tool calls. Optional
  bearer credentials are read
  at request time from `api_key_env`; literal keys and arbitrary headers are rejected by
  configuration. Repository context leaves the machine when a remote endpoint is used.
  A returned `model` is recorded as the backend's claim while the exact version remains
  `unknown_unverified_backend`; an omitted model is `unknown_no_telemetry`.
  Its timeout bounds each socket operation, not the full wall-clock request; callers that
  require a total deadline must supervise the Exhibit A process externally.

## Configuration

`exhibit-a repro --provider-config providers.json ...` selects the proposer through a
strict role assignment. Model-backed roles are allowlisted; `verifier` is deliberately
invalid because the deterministic judge is not a provider role. This first config surface
wires only `proposer`; other fallible model-assisted roles will be added when their CLI
workflows consume the same configuration rather than accepting inert settings.

```json
{
  "providers": {
    "local": {
      "type": "ollama",
      "model": "qwen3:8b",
      "base_url": "http://127.0.0.1:11434/v1",
      "roles": ["proposer"]
    }
  },
  "roles": {"proposer": "local"}
}
```

For a hosted or TLS-terminated OpenAI-compatible endpoint, keep the credential out of
the JSON file and name its environment variable instead:

```json
{
  "providers": {
    "hosted": {
      "type": "openai_compatible",
      "model": "deployment-alias",
      "base_url": "https://models.example.com/v1",
      "api_key_env": "HOSTED_MODEL_API_KEY",
      "roles": ["proposer"]
    }
  },
  "roles": {"proposer": "hosted"}
}
```
