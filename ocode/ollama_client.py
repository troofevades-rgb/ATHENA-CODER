"""Thin wrapper around the Ollama /api/chat endpoint with tool-calling support."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any, Iterator

import httpx


@dataclass
class ChatChunk:
    """One streamed chunk from /api/chat."""
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    done: bool = False
    raw: dict[str, Any] | None = None


class OllamaClient:
    def __init__(self, host: str, timeout: float = 600.0):
        self.host = host.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def list_models(self) -> list[str]:
        r = self._client.get(f"{self.host}/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def show_model(self, model: str) -> dict[str, Any]:
        """Fetch model metadata via /api/show. Returns dict with keys like
        'modelfile', 'parameters', 'template', 'system', 'details'.
        The 'system' field is the SYSTEM directive from the Modelfile, if any.
        """
        r = self._client.post(f"{self.host}/api/show", json={"name": model})
        r.raise_for_status()
        return r.json()

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        num_ctx: int | None = None,
    ) -> Iterator[ChatChunk]:
        """Stream a chat completion. Yields ChatChunk objects.

        Ollama's tool-call payloads come on a single chunk (not split across
        deltas like OpenAI), so we surface them whole when present.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if num_ctx:
            payload.setdefault("options", {})["num_ctx"] = num_ctx

        with self._client.stream(
            "POST", f"{self.host}/api/chat", json=payload
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                msg = obj.get("message", {}) or {}
                yield ChatChunk(
                    content=msg.get("content", "") or "",
                    tool_calls=msg.get("tool_calls"),
                    done=obj.get("done", False),
                    raw=obj,
                )

    def close(self) -> None:
        self._client.close()
