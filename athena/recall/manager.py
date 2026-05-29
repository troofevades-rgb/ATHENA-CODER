"""Recall manager — session-level glue (T6-01.3).

One thin coordinator that the Agent + tools can ask:

  * "give me a :class:`VectorStore` for this profile" — once-per-
    session lazy construction with the resolved embedder
  * "embed this turn / memory entry on write" — best-effort,
    silent fall-through when no embedder is configured

Plus a ContextVar that the recall tool layer can consult to find
the active store without dragging the agent through every call
site (mirroring the CheckpointManager pattern from T3-03).
"""

from __future__ import annotations

import contextvars
import logging
from pathlib import Path
from typing import Any

from .embeddings import resolve_embedder
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


_active_store: contextvars.ContextVar[VectorStore | None] = contextvars.ContextVar(
    "athena_vector_store", default=None
)


def get_active_vector_store() -> VectorStore | None:
    """Return the per-session vector store, or None when no
    semantic-recall machinery is active for the current context."""
    return _active_store.get()


def set_active_vector_store(store: VectorStore | None) -> None:
    _active_store.set(store)


def build_vector_store(*, cfg: Any, profile_dir: Path) -> VectorStore | None:
    """Construct the per-profile :class:`VectorStore` with a resolved
    embedder. Returns None when ``semantic_recall_enabled`` is False
    or no embeddings provider is registered — callers degrade to
    keyword-only without complaining."""
    if not getattr(cfg, "semantic_recall_enabled", True):
        return None
    embedder = resolve_embedder(cfg=cfg)
    if embedder is None:
        return None
    idx_path = getattr(cfg, "vector_store_path", None) or (Path(profile_dir) / "vectors.json")
    return VectorStore(path=Path(str(idx_path)), embedder=embedder)


# ---------------------------------------------------------------------------
# Doc-id conventions
# ---------------------------------------------------------------------------


def session_doc_id(session_id: str, turn_index: int) -> str:
    """Stable doc_id for a session turn: ``session_id#turn_index``.
    The store keys on this exact string so the hybrid path can
    hydrate session hits via the existing SessionStore lookup."""
    return f"{session_id}#{turn_index}"


def memory_doc_id(profile: str, name: str) -> str:
    """Stable doc_id for a memory entry. Profile-scoped so a
    cross-profile memory rename doesn't silently shadow."""
    return f"memory:{profile}:{name}"


def parse_session_doc_id(doc_id: str) -> tuple[str, int] | None:
    """Split a session doc_id back into ``(session_id, turn_index)``.
    Returns None for non-session ids (memory entries, malformed
    strings) — the caller decides whether that's "skip" or
    "look up elsewhere"."""
    if "#" not in doc_id or doc_id.startswith("memory:"):
        return None
    sid, _, idx = doc_id.rpartition("#")
    if not sid:
        return None
    try:
        return sid, int(idx)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Incremental embed-on-write
# ---------------------------------------------------------------------------


def record_turn(
    *,
    session_id: str,
    turn_index: int,
    role: str,
    content: str,
    workspace: str,
) -> None:
    """Best-effort incremental embedding of a session turn.

    Called by the Agent on every persisted assistant/user
    message. No active store → silent no-op (the contract is
    "never break the agent on a recall-side bug"). Skips empty /
    tool-result content; only embeds user + assistant text
    that's likely to be recall-worthy.
    """
    store = get_active_vector_store()
    if store is None:
        return
    if role not in ("user", "assistant"):
        return
    if not content:
        return
    if isinstance(content, list):
        # Anthropic-style multimodal content list — join the text
        # blocks for embedding purposes; non-text blocks are
        # skipped.
        parts = [
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        ]
        content = " ".join(p for p in parts if p)
    if not isinstance(content, str):
        return
    text = content.strip()
    if not text:
        return
    try:
        store.add(
            doc_id=session_doc_id(session_id, turn_index),
            text=text,
            workspace=workspace,
            text_preview=text[:200],
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("record_turn failed for %s#%s: %s", session_id, turn_index, e)


def record_memory_entry(
    *,
    profile: str,
    name: str,
    content: str,
    workspace: str = "",
) -> None:
    """Best-effort incremental embedding of a memory entry. Same
    contract as :func:`record_turn` — no store → no-op."""
    store = get_active_vector_store()
    if store is None:
        return
    text = (content or "").strip()
    if not text:
        return
    try:
        store.add(
            doc_id=memory_doc_id(profile, name),
            text=text,
            workspace=workspace,
            text_preview=text[:200],
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("record_memory_entry failed for %s/%s: %s", profile, name, e)
