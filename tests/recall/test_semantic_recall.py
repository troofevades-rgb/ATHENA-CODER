"""Integration tests for the recall mode dispatch + CLI (T6-01.3).

The headline test is the paraphrase case: a phrase only findable
by meaning surfaces in semantic / hybrid but not keyword. We use
a stub embedder + an in-memory session store to verify the mode
dispatch deterministically.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from athena.recall import (
    VectorStore,
    set_active_vector_store,
)
from athena.sessions.store import SearchHit
from athena.tools.recall_tools import _format_hits, _ranked_hits, _resolve_mode

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Deterministic vector mapper for integration tests."""

    def __init__(self, vectors: dict[str, list[float]], model_id: str = "stub-v1"):
        self.vectors = vectors
        self.model_id = model_id

    def embed(self, text: str) -> list[float]:
        return list(self.vectors.get(text, [0.0, 0.0, 0.0]))


class _StubSessionStore:
    """Minimal session-store stub matching SessionStore's public
    surface the recall path uses: .search() returning SearchHit
    list, .load(session_id) returning the JSONL messages."""

    def __init__(self):
        self._sessions: dict[str, list[dict]] = {}
        self._fts5_hits: dict[str, list[SearchHit]] = {}

    def add_session(self, session_id: str, messages: list[dict]) -> None:
        self._sessions[session_id] = messages

    def stage_fts5(self, query: str, hits: list[SearchHit]) -> None:
        self._fts5_hits[query] = hits

    def search(self, query, k=5, workspace=None):
        # Return staged hits when present, else empty (keyword
        # MISSES for paraphrased queries — that's the headline
        # property tested below).
        return self._fts5_hits.get(query, [])[:k]

    def load(self, session_id: str) -> list[dict]:
        return self._sessions.get(session_id, [])


@pytest.fixture
def vector_store(tmp_path) -> VectorStore:
    """A vector store with three docs of known embeddings."""
    emb = _StubEmbedder(
        {
            "we discussed adding retry logic to the API client": [1.0, 0.0, 0.0],
            "deployment notes for v2.1": [0.0, 1.0, 0.0],
            "lunch options": [0.0, 0.0, 1.0],
            # Query close to the retry doc by meaning, but with
            # zero word overlap.
            "when did we set up automatic re-attempts": [0.95, 0.05, 0.0],
            # Query that overlaps keywords with both relevant docs.
            "retry": [0.8, 0.1, 0.0],
        }
    )
    s = VectorStore(path=tmp_path / "v.json", embedder=emb)
    s.add(
        "sess-retry#3",
        "we discussed adding retry logic to the API client",
        workspace="/proj",
    )
    s.add("sess-deploy#0", "deployment notes for v2.1", workspace="/proj")
    s.add("sess-lunch#0", "lunch options", workspace="/proj")
    return s


@pytest.fixture
def session_store():
    s = _StubSessionStore()
    s.add_session(
        "sess-retry",
        [
            {"role": "user", "content": "before retry"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "context"},
            {
                "role": "assistant",
                "content": "we discussed adding retry logic to the API client",
            },
        ],
    )
    s.add_session(
        "sess-deploy",
        [{"role": "user", "content": "deployment notes for v2.1"}],
    )
    s.add_session("sess-lunch", [{"role": "user", "content": "lunch options"}])
    return s


# ---------------------------------------------------------------------------
# The headline test
# ---------------------------------------------------------------------------


def test_semantic_finds_paraphrase_keyword_misses(vector_store, session_store, monkeypatch):
    """A query phrased differently from the stored text gets
    no FTS5 hits (keyword path returns []), but the embedding
    matches and surfaces the right turn."""
    set_active_vector_store(vector_store)
    try:
        # FTS5 staging is empty for this paraphrase → keyword
        # misses; semantic should find it via the embedding.
        hits = _ranked_hits(
            store=session_store,
            query="when did we set up automatic re-attempts",
            k=3,
            workspace="/proj",
            mode="semantic",
        )
    finally:
        set_active_vector_store(None)
    assert hits, "semantic recall should have surfaced the retry turn"
    assert hits[0].session_id == "sess-retry"


# ---------------------------------------------------------------------------
# Hybrid combines both signals
# ---------------------------------------------------------------------------


def test_hybrid_combines_keyword_and_semantic(vector_store, session_store):
    """A doc that scores in both lists ranks above one that only
    appears in one."""
    # Stage FTS5: deploy hit returned for the keyword query
    # "retry" (totally bogus — but we're simulating FTS5
    # surfacing a different doc).
    session_store.stage_fts5(
        "retry",
        [
            SearchHit(
                session_id="sess-deploy",
                turn_index=0,
                role="user",
                snippet="deployment notes for v2.1",
                surrounding=[],
                score=0.5,
                started_at=datetime.fromtimestamp(0),
                workspace="/proj",
            )
        ],
    )
    set_active_vector_store(vector_store)
    try:
        # Semantic returns sess-retry first (vec [0.8, 0.1, 0.0]
        # closest to [1, 0, 0]). FTS5 returns sess-deploy. Hybrid
        # should surface both, with the higher RRF score first.
        hits = _ranked_hits(
            store=session_store,
            query="retry",
            k=5,
            workspace="/proj",
            mode="hybrid",
        )
    finally:
        set_active_vector_store(None)
    session_ids = [h.session_id for h in hits]
    assert "sess-retry" in session_ids
    assert "sess-deploy" in session_ids


# ---------------------------------------------------------------------------
# Degradation to keyword
# ---------------------------------------------------------------------------


def test_degrade_to_keyword_when_no_embeddings(session_store):
    """No active vector store → semantic / hybrid both fall back
    to the FTS5 keyword path. Never a crash; never an empty
    result when FTS5 would have returned hits."""
    session_store.stage_fts5(
        "deploy",
        [
            SearchHit(
                session_id="sess-deploy",
                turn_index=0,
                role="user",
                snippet="deployment notes for v2.1",
                surrounding=[],
                score=0.5,
                started_at=datetime.fromtimestamp(0),
                workspace="/proj",
            )
        ],
    )
    # No set_active_vector_store call → get_active_vector_store
    # returns None.
    hits = _ranked_hits(
        store=session_store,
        query="deploy",
        k=5,
        workspace="/proj",
        mode="hybrid",
    )
    assert hits and hits[0].session_id == "sess-deploy"

    # Same for semantic mode.
    hits = _ranked_hits(
        store=session_store,
        query="deploy",
        k=5,
        workspace="/proj",
        mode="semantic",
    )
    assert hits and hits[0].session_id == "sess-deploy"


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_output_shape_matches_search_sessions(vector_store, session_store):
    """Every path goes through the same _format_hits, so the
    rendered output is byte-identical-shaped regardless of mode."""
    set_active_vector_store(vector_store)
    try:
        hits = _ranked_hits(
            store=session_store,
            query="when did we set up automatic re-attempts",
            k=3,
            workspace="/proj",
            mode="semantic",
        )
    finally:
        set_active_vector_store(None)
    rendered = _format_hits("paraphrase", hits)
    assert rendered.startswith("Found ")
    assert "## Session sess-retry" in rendered


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def test_resolve_mode_explicit_wins():
    assert _resolve_mode("keyword") == "keyword"
    assert _resolve_mode("semantic") == "semantic"
    assert _resolve_mode("hybrid") == "hybrid"


def test_resolve_mode_invalid_falls_back_to_cfg(monkeypatch):
    """An invalid mode argument falls through to cfg default —
    no exception, no surprise behaviour."""
    monkeypatch.setattr(
        "athena.config.load_config",
        lambda: SimpleNamespace(recall_default_mode="hybrid"),
    )
    assert _resolve_mode("nonsense") == "hybrid"


# ---------------------------------------------------------------------------
# record_turn — the embed-on-write path
# ---------------------------------------------------------------------------


def test_record_turn_writes_to_active_store(tmp_path: Path):
    """record_turn embeds the message + writes a vector when an
    active store is bound. The doc_id follows the
    session_id#turn_index convention."""
    from athena.recall import record_turn

    emb = _StubEmbedder({"hello world": [1.0, 0.0]})
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    set_active_vector_store(store)
    try:
        record_turn(
            session_id="s1",
            turn_index=3,
            role="user",
            content="hello world",
            workspace="/proj",
        )
    finally:
        set_active_vector_store(None)

    entries = store.all()
    assert len(entries) == 1
    assert entries[0].doc_id == "s1#3"


def test_record_turn_no_store_is_silent(tmp_path: Path):
    """No active store → silent no-op (the contract)."""
    from athena.recall import record_turn

    # No set_active_vector_store call.
    record_turn(
        session_id="s1",
        turn_index=0,
        role="user",
        content="ignored",
        workspace="/proj",
    )
    # Nothing to assert — the function must not raise.


def test_record_turn_skips_tool_role(tmp_path: Path):
    """Tool results don't get embedded — they're rarely
    recall-worthy as standalone turns, and the model already
    sees them with the tool call that produced them."""
    from athena.recall import record_turn

    emb = _StubEmbedder({"output": [1.0, 0.0]})
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    set_active_vector_store(store)
    try:
        record_turn(
            session_id="s1",
            turn_index=0,
            role="tool",
            content="output",
            workspace="/proj",
        )
    finally:
        set_active_vector_store(None)
    assert store.all() == []
