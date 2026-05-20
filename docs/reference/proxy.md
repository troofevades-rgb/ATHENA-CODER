# athena proxy

`athena proxy serve` runs a local OpenAI-compatible HTTP endpoint
backed by athena's full provider stack — `resolve_provider`'s
prefix/config routing + credential fallback chains, prompt
caching, retry, rate-limit tracking. Any third-party tool that
speaks OpenAI Chat Completions can use athena as its backend
without learning a new CLI.

Built on the same aiohttp stack as the gateway and webhook
listener, with a Phase-16 observability span emitted per call.

## Install

The proxy ships under an optional extra (aiohttp is the only
dependency on top of the core install):

```bash
pipx install --force "athena-coder[proxy]"
```

Headless installs that already pull `[gateway]` get aiohttp for
free and don't need the `[proxy]` extra.

## Quick start

```bash
# Terminal 1
athena proxy serve

# Terminal 2 — point any OpenAI-compatible tool at the proxy
aider \
  --openai-api-base http://localhost:11434/v1 \
  --openai-api-key dummy \
  --model claude-sonnet-4-6
```

Default port is `11434` — Ollama's default. The proxy slots in
cleanly when Ollama isn't running, and any tool already configured
for Ollama works against athena unchanged.

## How requests get routed

`/v1/chat/completions` goes through three resolution steps:

1. `X-Athena-Provider: <name>` header (case-insensitive) if the
   named provider has credentials in the pool.
2. Model-name match — `claude-*` → anthropic, `gpt-*` → openai,
   `gemini-*` → google.
3. Fall back to `cfg.proxy_default_provider`.

Once a provider is chosen, athena's existing
`resolve_provider(model, cfg, pool)` (the same call the agent
uses) builds the concrete `Provider` instance, applying any
configured prefix/fallback rules and credential rotation.

Returns:

- `400` with `type=invalid_request_error` if no provider claims
  the request and the default isn't available.
- `503` with `type=provider_unavailable` if the resolver raises
  RuntimeError (no credentials).
- `502` with `type=upstream_error` if the provider itself fails
  mid-call.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET    | /health | Operator probe + snapshot of available providers |
| GET    | /v1/models | Union of every available provider's catalog |
| POST   | /v1/chat/completions | OpenAI Chat Completions, streaming + non-streaming |
| POST   | /v1/embeddings | 501 — not implemented |

## Configuration

```toml
proxy_default_provider = "anthropic"
proxy_bind_host = "127.0.0.1"
proxy_bind_port = 11434
proxy_log_path = "~/.athena/proxy.jsonl"
proxy_log_bodies = false
proxy_bodies_dir = "~/.athena/proxy_bodies"
```

CLI flags override config: `--host`, `--port`, `--provider`,
`--log-bodies`. `--bind-public` is the only way to bind `0.0.0.0`
(defense-in-depth: the proxy uses your API keys to fulfil any
request that reaches it).

## What gets logged

### Phase-16 observability span (per call)

Every `/v1/chat/completions` call emits an `athena.proxy.call`
span with attributes:

- `athena.proxy.provider`, `athena.proxy.model`
- `athena.proxy.requested_model`, `athena.proxy.stream`
- `athena.proxy.client_ua`
- `athena.proxy.tokens.prompt`, `athena.proxy.tokens.completion`
- `athena.proxy.latency_ms`

Inert when the `[observability]` extra isn't installed; emitted
via the configured OTel exporter when it is.

### Per-request JSONL line (always)

One line per completed request in `~/.athena/proxy.jsonl`:

```json
{
  "request_id": "chatcmpl-...",
  "ts": "2026-05-20T...Z",
  "client_ua": "Aider/0.51.0",
  "model_requested": "claude-sonnet-4-6",
  "provider_used": "anthropic",
  "latency_ms": 1234.5,
  "tokens_in": 1500,
  "tokens_out": 320,
  "cache_read_tokens": 1280,
  "request_summary": {"message_count": 4, "has_tools": true, "stream": true},
  "response_summary": {"finish_reason": "tool_calls", "has_tool_calls": true}
}
```

### Opt-in full-body capture

With `--log-bodies` (or `proxy_log_bodies = true`), full request
and response payloads land at
`~/.athena/proxy_bodies/<request_id>.json`. Useful for debugging
translation issues; bodies can exceed 50 KB so they're opt-in.

## T6-03 use case — reverse-proxy delegation

The proxy is the building block for T6-03's "athena fronts an
external OpenAI-compat CLI" pattern. Point a third-party CLI
(Aider, Cline, Codex CLI, Continue, the OpenAI Python SDK) at
`http://localhost:11434/v1` and athena brokers every call —
prefix/config routing applies, credential rotation applies,
Phase-16 spans flow into the same telemetry pipeline as native
agent calls, and ProxyLogger captures the full trajectory for
training-data review.

## Translation

`athena/proxy/translate.py` is a pure, framework-free module that
maps athena's canonical `Iterator[StreamChunk]` to OpenAI Chat
Completions wire format:

- `content` chunks → `choices[0].delta.content` (SSE) / joined
  into `message.content` (non-streaming)
- `tool_call` chunks → `choices[0].delta.tool_calls[i].function.{name,arguments}`
- `usage` chunks → top-level `usage` (non-streaming) / accumulated
  via `on_usage` callback for the proxy log
- `end` chunks → `choices[0].finish_reason` (mapped:
  `end_turn→stop`, `tool_use→tool_calls`, `max_tokens→length`)

Stream output always starts with a `role=assistant` delta and
always ends with `data: [DONE]\n\n`.

## Limitations

- `--no-translate` flag is reserved; no passthrough mode yet.
- Embeddings endpoint stubbed (501).
- Reasoning tokens (o1/o3 style, Anthropic extended thinking)
  pass through where shapes align and drop otherwise.
- No proxy-side context compression — the proxy is stateless per
  request.

## Security

- 127.0.0.1 by default; `--bind-public` is the only way to expose
  the listener.
- The proxy doesn't validate any `Authorization: Bearer` token
  clients send (it has nothing to authenticate against); the
  loopback socket is the security boundary.
- Provider API keys never appear in proxy logs or response bodies.

## Smoke testing

Two runbooks in `docs/proof/` walk through manual integration
tests:

- `proxy-aider-runbook.md` — Aider, the canonical client
- `proxy-clients-runbook.md` — Cline, Codex CLI, Continue, OpenAI SDK
