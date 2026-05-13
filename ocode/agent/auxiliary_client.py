"""Per-fork provider client construction.

A fork that shares the parent's HTTP client (1) competes for connection-pool
slots and (2) invalidates the parent's KV cache on most providers. So forks
default to building their own client, configured identically to the parent's.

Phase 3 supports only the in-tree Ollama client. The provider abstraction in
Phase 8 will turn this into a registry lookup keyed by provider name.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..ollama_client import OllamaClient

if TYPE_CHECKING:
    from .core import Agent


def build_auxiliary_client(parent_agent: "Agent"):
    """Return a fresh client configured identically to ``parent_agent``'s.

    The function inspects the parent's runtime configuration (currently
    ``cfg.ollama_host``) and constructs a parallel client. When the provider
    abstraction lands the dispatch will key off ``parent_agent.provider.name``.
    """
    return OllamaClient(parent_agent.cfg.ollama_host)
