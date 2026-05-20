"""Semantic recall (T6-01).

Layer on top of athena's existing FTS5-backed ``search_sessions``
that adds embedding-based retrieval and a reciprocal-rank-fusion
hybrid ranker. Resolves an embeddings provider via the T5-01
capability manifest — local-preferred so the entire recall path
can run offline with a local embedding model.

The keyword path is untouched; semantic is additive. Falls back
to keyword-only when no embeddings provider is available, so
recall never breaks because of a missing optional component.
"""

from .embeddings import Embedder, resolve_embedder
from .hybrid import rrf_fuse
from .manager import (
    build_vector_store,
    get_active_vector_store,
    memory_doc_id,
    parse_session_doc_id,
    record_memory_entry,
    record_turn,
    session_doc_id,
    set_active_vector_store,
)
from .vector_store import VectorEntry, VectorStore

__all__ = [
    "Embedder",
    "VectorEntry",
    "VectorStore",
    "build_vector_store",
    "get_active_vector_store",
    "memory_doc_id",
    "parse_session_doc_id",
    "record_memory_entry",
    "record_turn",
    "resolve_embedder",
    "rrf_fuse",
    "session_doc_id",
    "set_active_vector_store",
]
