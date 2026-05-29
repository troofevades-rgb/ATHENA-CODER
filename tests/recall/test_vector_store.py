"""Tests for the local vector store (T6-01.2).

The store is the bookkeeping; tests use a stub embedder that
returns deterministic vectors so the assertions check the
store's filtering, ordering, and persistence — not embedding
quality.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athena.recall.vector_store import VectorStore


class _StubEmbedder:
    """Deterministic embedder for store tests.

    ``vectors`` is a dict ``text → list[float]``. ``model_id``
    is fixed at construction; pass a different one to simulate
    a model swap."""

    def __init__(self, vectors: dict[str, list[float]], model_id: str = "stub-v1"):
        self.vectors = vectors
        self.model_id = model_id

    def embed(self, text: str) -> list[float]:
        return list(self.vectors.get(text, [0.0, 0.0, 0.0]))


# ---------------------------------------------------------------------------
# Add / search
# ---------------------------------------------------------------------------


def test_add_and_search_returns_nearest_first(tmp_path: Path):
    emb = _StubEmbedder(
        {
            "apple pie recipe": [1.0, 0.0, 0.0],
            "banana bread tips": [0.0, 1.0, 0.0],
            "cosmic background": [0.0, 0.0, 1.0],
            # Query close to the "apple" vector
            "best apple desserts": [0.9, 0.1, 0.0],
        }
    )
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    store.add("d1", "apple pie recipe", workspace="/proj")
    store.add("d2", "banana bread tips", workspace="/proj")
    store.add("d3", "cosmic background", workspace="/proj")

    hits = store.search("best apple desserts", k=2, workspace="/proj")
    assert hits[0] == "d1"  # nearest


def test_search_empty_store_returns_empty(tmp_path: Path):
    """No entries → no hits, even with a valid query."""
    emb = _StubEmbedder({"query": [1.0, 0.0]})
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    assert store.search("query", k=5, workspace=None) == []


def test_search_no_embedder_returns_empty(tmp_path: Path):
    """Without an embedder we can't embed the query — return
    empty rather than crashing. The caller's hybrid path then
    falls back to keyword-only."""
    store = VectorStore(path=tmp_path / "v.json", embedder=None)
    assert store.search("anything", k=5) == []


# ---------------------------------------------------------------------------
# Model ID isolation — the load-bearing safety property
# ---------------------------------------------------------------------------


def test_respects_model_id(tmp_path: Path):
    """An entry embedded by model X must NOT be returned when
    the embedder is currently using model Y — mixing spaces
    silently is the worst-case failure for recall."""
    emb_v1 = _StubEmbedder(
        {"hello": [1.0, 0.0], "world": [0.0, 1.0], "query": [1.0, 0.0]},
        model_id="v1",
    )
    store = VectorStore(path=tmp_path / "v.json", embedder=emb_v1)
    store.add("doc-from-v1", "hello", workspace="/proj")

    # Swap to v2 embedder; even though "hello" still embeds as
    # the same numbers in this fixture, the model_id filter
    # excludes the v1 entry.
    emb_v2 = _StubEmbedder({"hello": [1.0, 0.0], "query": [1.0, 0.0]}, model_id="v2")
    store.embedder = emb_v2
    hits = store.search("query", k=5, workspace="/proj")
    assert hits == []  # v1 entry excluded


# ---------------------------------------------------------------------------
# Workspace filter
# ---------------------------------------------------------------------------


def test_workspace_filter(tmp_path: Path):
    """Workspace filter excludes other-project entries."""
    emb = _StubEmbedder(
        {
            "thing": [1.0, 0.0],
            "query": [1.0, 0.0],
        }
    )
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    store.add("a", "thing", workspace="/proj/a")
    store.add("b", "thing", workspace="/proj/b")

    hits = store.search("query", k=5, workspace="/proj/a")
    assert hits == ["a"]


def test_workspace_none_is_unscoped(tmp_path: Path):
    """workspace=None on search returns hits from any workspace."""
    emb = _StubEmbedder({"thing": [1.0, 0.0], "query": [1.0, 0.0]})
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    store.add("a", "thing", workspace="/proj/a")
    store.add("b", "thing", workspace="/proj/b")

    hits = store.search("query", k=5, workspace=None)
    assert set(hits) == {"a", "b"}


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def test_backfill_counts(tmp_path: Path):
    """Backfill returns the number of entries actually written
    (skipping empty / unembeddable texts)."""
    emb = _StubEmbedder(
        {
            "a": [1.0, 0.0],
            "b": [0.0, 1.0],
            "c": [1.0, 1.0],
        }
    )
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    n = store.backfill(
        [
            ("d1", "a", "/proj"),
            ("d2", "", "/proj"),  # empty — skipped
            ("d3", "c", "/proj"),
        ]
    )
    assert n == 2
    assert len(store.all()) == 2


# ---------------------------------------------------------------------------
# Upsert semantics + persistence
# ---------------------------------------------------------------------------


def test_add_overwrites_same_doc_id(tmp_path: Path):
    """Adding a doc_id that already exists replaces the entry —
    for memory entries that get rewritten."""
    emb = _StubEmbedder({"old text": [1.0, 0.0], "new text": [0.0, 1.0]})
    store = VectorStore(path=tmp_path / "v.json", embedder=emb)
    store.add("doc", "old text", workspace="/proj")
    store.add("doc", "new text", workspace="/proj")
    entries = store.all()
    assert len(entries) == 1
    assert entries[0].vector == [0.0, 1.0]


def test_persistence_roundtrip(tmp_path: Path):
    """An entry written by one VectorStore is visible to a fresh
    one reading the same file."""
    emb = _StubEmbedder({"persistent thing": [1.0, 0.0]})
    store_a = VectorStore(path=tmp_path / "v.json", embedder=emb)
    store_a.add("doc1", "persistent thing", workspace="/proj")

    store_b = VectorStore(path=tmp_path / "v.json", embedder=emb)
    hits = store_b.search("persistent thing", k=5, workspace="/proj")
    assert hits == ["doc1"]


def test_corrupt_file_starts_fresh(tmp_path: Path):
    """A malformed vectors.json doesn't crash construction — the
    store silently starts empty. The next add() overwrites the
    bad file."""
    path = tmp_path / "v.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    emb = _StubEmbedder({"thing": [1.0, 0.0]})
    store = VectorStore(path=path, embedder=emb)
    assert store.all() == []
    store.add("doc1", "thing", workspace="/proj")
    assert len(store.all()) == 1
