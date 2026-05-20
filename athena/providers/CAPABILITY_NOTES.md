# T5-01R recon — capability manifest seam

## Files that hold each provider class

| Provider | File | Class |
|---|---|---|
| ollama | `athena/providers/ollama.py` | `OllamaProvider` |
| anthropic | `athena/providers/anthropic.py` | `AnthropicProvider` |
| openai | `athena/providers/openai.py` | `OpenAIProvider` |
| google | `athena/providers/google.py` | `GoogleProvider` |
| openrouter | `athena/providers/openrouter.py` | `OpenRouterProvider` |
| nous | `athena/providers/nous.py` | `NousProvider` |
| openai_compat | `athena/providers/openai_compat.py` | `OpenAICompatProvider` |

Each one registers itself via `@register_provider` at import time;
`_REGISTRY` in `athena/providers/__init__.py` ends up populated.

## Minimal constructor args (for tests + parity)

| Provider | Required positional / keyword | Notes |
|---|---|---|
| ollama | `(host="http://x")` | no api_key, host optional |
| anthropic | `(api_key="…")` | required positional |
| openai | `(api_key="…")` | optional, but pass one |
| google | `(api_key="…")` | required positional |
| openrouter | `(api_key="…")` | required positional |
| nous | `(api_key="…")` | required positional |
| openai_compat | `(api_key=None, host="http://x")` | host is required kwarg |

Tests can construct each with dummy strings — no network is made
at __init__ (each holds an httpx.Client config but doesn't fire).

## `supports_tools` / `supports_streaming` today

Defined only in `Provider` (base.py:104, 107). Both return `True`
unconditionally. No provider overrides them. So every provider's
instance currently returns `True` for any model.

The parity test materialises one instance per registered provider
and pins `supports_tools("any-model") is True` +
`supports_streaming("any-model") is True`.

## File backing `athena providers`

`athena/cli/providers.py:main` — wires `list`, `add-key`,
`remove-key`, `cooldowns`, `rate-state`. The capability matrix view
goes here as a new action (no top-level command added).

`add-key` lives at lines 158-188 (roughly); it constructs a
`Credential` and calls `pool.add_credential`. Untouched by T5-01R.

## Plan (per spec)

1. Pin the parity baseline NOW (`test_supports_parity.py`).
2. Add `Capabilities` dataclass + `static_capabilities` /
   `capabilities` to `Provider`; fold `supports_*` into delegators
   reading the manifest. Re-run parity (green = behaviour unchanged).
3. Declare honest manifests on each provider.
4. Add registry helpers (`capability_matrix`, `providers_with_capability`,
   `best_provider_for`, credential-aware `available_providers_with_capability`).
5. Extend `athena providers` with a matrix view + `--json`.
6. CHANGELOG.

`resolve_provider` / `CredentialPool` are untouched. The
`@register_provider` mechanism stays — no entry points.
