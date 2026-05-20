"""FastAPI server for ``athena proxy`` (T3-01.5).

Exposes:

- ``GET  /v1/models`` — union of models from every provider that has
  at least one credential in the pool (plus ollama, which doesn't
  need one).
- ``POST /v1/chat/completions`` — OpenAI Chat Completions API. The
  body's ``model`` field is routed via
  :func:`athena.proxy.router.route_request` to a concrete provider;
  athena's :func:`athena.providers.runtime_resolver._build_provider`
  constructs the provider on demand.
- ``POST /v1/embeddings`` — stubbed; returns 501.

The server uses athena's canonical
:class:`athena.providers.base.StreamChunk` stream as the wire format
between provider and SSE translator. The dedicated
``openai_request_to_anthropic`` / ``anthropic_stream_to_openai_chunks``
helpers in :mod:`athena.proxy.translator` remain tested utilities
for a future ``--no-translate`` deep-integration mode that bypasses
the provider abstraction; the default path keeps caching, retry,
and rate-limit tracking by going through ``provider.stream_chat``.

FastAPI is declared under the ``proxy`` optional-extra; importing
this module without ``pip install athena-coder[proxy]`` raises at
:func:`make_app`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..providers.base import Provider, StreamChunk
from .logging import ProxyLogger
from .router import RouteError, route_request

if TYPE_CHECKING:
    from ..config import Config
    from ..providers.credential_pool import CredentialPool

logger = logging.getLogger(__name__)


# FastAPI is an optional dependency declared under the ``proxy``
# extra. Import at module level so the request handlers' annotations
# (resolved by FastAPI via ``get_type_hints``) point at the real
# ``Request`` class — lazy-importing inside ``make_app`` makes
# annotations unresolvable under ``from __future__ import annotations``
# and FastAPI mistakes the ``request`` parameter for a query field.
# Capture an import error and re-raise on call so a headless install
# without the extra still imports cleanly.
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    _FASTAPI_IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    JSONResponse = None  # type: ignore[assignment,misc]
    StreamingResponse = None  # type: ignore[assignment,misc]
    _FASTAPI_IMPORT_ERROR = _e


# ---------------------------------------------------------------------------
# StreamChunk → OpenAI SSE
# ---------------------------------------------------------------------------


_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "end_turn": "stop",
    "length": "length",
    "max_tokens": "length",
    "tool_calls": "tool_calls",
    "tool_use": "tool_calls",
    "stop_sequence": "stop",
}


def _openai_finish_reason(raw: str) -> str:
    return _FINISH_REASON_MAP.get(raw, "stop")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def stream_chunks_to_openai_sse(
    chunks: Iterator[StreamChunk],
    *,
    model: str,
    request_id: str,
    on_usage: Callable[[int, int, int], None] | None = None,
) -> Iterator[str]:
    """Convert athena's canonical :class:`StreamChunk` iterator into
    OpenAI Chat Completions SSE strings. ``on_usage`` is invoked with
    ``(input_tokens, output_tokens, cache_read_tokens)`` if the
    provider emits a ``usage`` chunk."""
    created = int(time.time())
    finish_emitted = False

    # Opening role chunk.
    yield _sse(
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    )

    tool_index_by_id: dict[str, int] = {}
    next_tool_index = 0

    for chunk in chunks:
        kind = chunk.kind
        payload = chunk.payload

        if kind == "content" and isinstance(payload, str) and payload:
            yield _sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": payload},
                            "finish_reason": None,
                        }
                    ],
                }
            )

        elif kind == "tool_call" and isinstance(payload, dict):
            tool_id = str(payload.get("id") or f"call_{uuid.uuid4().hex[:12]}")
            if tool_id not in tool_index_by_id:
                tool_index_by_id[tool_id] = next_tool_index
                next_tool_index += 1
            idx = tool_index_by_id[tool_id]
            raw_args = payload.get("arguments", "")
            args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
            yield _sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": idx,
                                        "id": tool_id,
                                        "type": "function",
                                        "function": {
                                            "name": str(payload.get("name", "")),
                                            "arguments": args_str,
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )

        elif kind == "usage" and isinstance(payload, dict):
            if on_usage is not None:
                on_usage(
                    int(payload.get("prompt_tokens", 0)),
                    int(payload.get("completion_tokens", 0)),
                    int(payload.get("cache_read_input_tokens", 0)),
                )

        elif kind == "end" and isinstance(payload, dict):
            reason = str(payload.get("reason") or "stop")
            yield _sse(
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": _openai_finish_reason(reason),
                        }
                    ],
                }
            )
            finish_emitted = True

    if not finish_emitted:
        # Streams that ended without an explicit ``end`` chunk still
        # need a terminating finish_reason for OpenAI clients.
        yield _sse(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )

    yield "data: [DONE]\n\n"


def collect_chunks_to_openai_response(
    chunks: Iterator[StreamChunk],
    *,
    model: str,
    request_id: str,
) -> dict[str, Any]:
    """Drain a :class:`StreamChunk` iterator and build the
    non-streaming OpenAI Chat Completions response shape."""
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish: str = "stop"
    prompt_tokens = 0
    completion_tokens = 0

    for chunk in chunks:
        if chunk.kind == "content" and isinstance(chunk.payload, str):
            content_parts.append(chunk.payload)
        elif chunk.kind == "tool_call" and isinstance(chunk.payload, dict):
            args = chunk.payload.get("arguments", "")
            args_str = args if isinstance(args, str) else json.dumps(args)
            tool_calls.append(
                {
                    "id": str(chunk.payload.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                    "type": "function",
                    "function": {
                        "name": str(chunk.payload.get("name", "")),
                        "arguments": args_str,
                    },
                }
            )
        elif chunk.kind == "usage" and isinstance(chunk.payload, dict):
            prompt_tokens = int(chunk.payload.get("prompt_tokens", 0))
            completion_tokens = int(chunk.payload.get("completion_tokens", 0))
        elif chunk.kind == "end" and isinstance(chunk.payload, dict):
            finish = _openai_finish_reason(str(chunk.payload.get("reason") or "stop"))

    message: dict[str, Any] = {"role": "assistant"}
    message["content"] = "".join(content_parts) if content_parts else None
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def make_app(
    *,
    cfg: Config,
    pool: CredentialPool,
    provider_factory: Callable[[str, str], tuple[Provider, str]] | None = None,
) -> Any:
    """Build the FastAPI app.

    ``provider_factory`` is ``(provider_name, requested_model) -> (Provider, resolved_model)``.
    Defaults to a closure over
    :func:`athena.providers.runtime_resolver._build_provider`. Tests
    inject a factory returning a stub provider so they don't need
    network access.
    """
    if _FASTAPI_IMPORT_ERROR is not None:
        raise RuntimeError(
            "athena proxy requires FastAPI. Install with:\n\n"
            '    pipx install --force "athena-coder[proxy]"\n'
        ) from _FASTAPI_IMPORT_ERROR

    from ..providers import list_providers as registered_providers
    from ..providers.runtime_resolver import _build_provider as _runtime_build

    if provider_factory is None:

        def _default_factory(name: str, model: str) -> tuple[Provider, str]:
            return _runtime_build(name, model, cfg, pool)

        provider_factory = _default_factory

    proxy_log = ProxyLogger(
        log_path=Path(cfg.proxy_log_path).expanduser(),
        bodies_dir=Path(cfg.proxy_bodies_dir).expanduser(),
        log_bodies=cfg.proxy_log_bodies,
    )

    app = FastAPI(title="athena proxy", version="1.0")

    def _available_providers() -> list[str]:
        """Providers that can serve a request right now: any in the
        credential pool plus ollama (no credentials required) and
        openai_compat (host-configured, may still work without a key)."""
        registered = set(registered_providers())
        with_creds = set(pool.providers())
        out: set[str] = set(with_creds)
        if "ollama" in registered:
            out.add("ollama")
        return sorted(out)

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        for provider_name in _available_providers():
            try:
                provider, _ = provider_factory(provider_name, "")
            except Exception as e:  # noqa: BLE001
                logger.debug("skipping %s in /v1/models: %s", provider_name, e)
                continue
            try:
                models = provider.list_models()
            except Exception as e:  # noqa: BLE001
                logger.info("/v1/models: %s list_models() failed: %s", provider_name, e)
                models = []
            finally:
                try:
                    provider.close()
                except Exception:
                    pass
            for m in models:
                data.append(
                    {
                        "id": m,
                        "object": "model",
                        "created": 0,
                        "owned_by": provider_name,
                    }
                )
        return {"object": "list", "data": data}

    @app.post("/v1/embeddings")
    def embeddings_stub() -> Any:
        # Some clients probe this endpoint; surface a clear "not yet"
        # rather than 404 so the user knows the proxy is reachable.
        raise HTTPException(
            status_code=501,
            detail="athena proxy does not implement /v1/embeddings yet. Tracked at T3-01 notes.",
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body: dict[str, Any] = await request.json()
        request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        client_ua = request.headers.get("user-agent", "")
        provider_header = request.headers.get("x-athena-provider")
        requested_model = str(body.get("model") or "")
        stream = bool(body.get("stream", False))

        try:
            provider_name, _ = route_request(
                requested_model=requested_model,
                provider_header=provider_header,
                default_provider=cfg.proxy_default_provider,
                available_providers=_available_providers(),
            )
        except RouteError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        try:
            provider, resolved_model = provider_factory(provider_name, requested_model)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"failed to build provider {provider_name!r}: {e}",
            ) from e

        stream_kwargs = _build_stream_kwargs(body, resolved_model)
        start = time.time()

        if stream:
            return StreamingResponse(
                _stream_response(
                    provider=provider,
                    stream_kwargs=stream_kwargs,
                    body=body,
                    request_id=request_id,
                    client_ua=client_ua,
                    model_requested=requested_model,
                    provider_used=provider_name,
                    proxy_log=proxy_log,
                    resolved_model=resolved_model,
                    start=start,
                ),
                media_type="text/event-stream",
            )

        # Non-streaming: collect and translate.
        try:
            chunks_iter = provider.stream_chat(**stream_kwargs)
            response = collect_chunks_to_openai_response(
                chunks_iter, model=resolved_model, request_id=request_id
            )
        except Exception as e:  # noqa: BLE001
            try:
                provider.close()
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=str(e)) from e

        try:
            provider.close()
        except Exception:
            pass

        latency_ms = (time.time() - start) * 1000.0
        usage = response.get("usage", {}) or {}
        proxy_log.log_completed(
            request_id=request_id,
            client_ua=client_ua,
            model_requested=requested_model,
            provider_used=provider_name,
            body=body,
            response=response,
            latency_ms=latency_ms,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
        )
        return JSONResponse(response)

    return app


def _build_stream_kwargs(body: dict[str, Any], resolved_model: str) -> dict[str, Any]:
    """Pull the kwargs accepted by :meth:`Provider.stream_chat` out of
    an incoming OpenAI request body. Unknown fields are dropped so the
    sync provider signature stays clean."""
    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "messages": body.get("messages") or [],
    }
    if "tools" in body and body["tools"]:
        kwargs["tools"] = body["tools"]
    if "temperature" in body and body["temperature"] is not None:
        kwargs["temperature"] = float(body["temperature"])
    if "max_tokens" in body and body["max_tokens"] is not None:
        kwargs["max_tokens"] = int(body["max_tokens"])
    return kwargs


def _stream_response(
    *,
    provider: Provider,
    stream_kwargs: dict[str, Any],
    body: dict[str, Any],
    request_id: str,
    client_ua: str,
    model_requested: str,
    provider_used: str,
    proxy_log: ProxyLogger,
    resolved_model: str,
    start: float,
) -> Iterator[bytes]:
    """Sync generator yielding SSE bytes. FastAPI's StreamingResponse
    runs sync generators in a threadpool, which is exactly what we
    want — athena providers are synchronous and would block the event
    loop if called from an async generator without ``run_in_threadpool``."""
    with proxy_log.scope(
        request_id=request_id,
        client_ua=client_ua,
        model_requested=model_requested,
        provider_used=provider_used,
        body=body,
    ) as scope:
        try:
            chunks_iter = provider.stream_chat(**stream_kwargs)
        except Exception as e:  # noqa: BLE001
            err_payload = {
                "error": {
                    "message": str(e),
                    "type": "upstream_error",
                    "code": type(e).__name__,
                }
            }
            yield _sse(err_payload).encode("utf-8")
            yield b"data: [DONE]\n\n"
            try:
                provider.close()
            except Exception:
                pass
            return

        def _capture_usage(p_in: int, p_out: int, p_cache: int) -> None:
            scope.add_tokens(in_=p_in, out=p_out, cache=p_cache)

        try:
            for sse_str in stream_chunks_to_openai_sse(
                chunks_iter,
                model=resolved_model,
                request_id=request_id,
                on_usage=_capture_usage,
            ):
                yield sse_str.encode("utf-8")
        except Exception as e:  # noqa: BLE001
            err_payload = {
                "error": {
                    "message": str(e),
                    "type": "stream_error",
                    "code": type(e).__name__,
                }
            }
            yield _sse(err_payload).encode("utf-8")
            yield b"data: [DONE]\n\n"
        finally:
            scope.set_latency(time.time() - start)
            try:
                provider.close()
            except Exception:
                pass
