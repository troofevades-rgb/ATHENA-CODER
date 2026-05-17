"""Per-profile session store: JSONL append + SQLite FTS5 mirror.

Plain files are the source of truth at
``~/.athena/profiles/<profile>/sessions/<session_id>.jsonl``. SQLite at
``~/.athena/profiles/<profile>/sessions.db`` is a cache — losing it doesn't
lose data, ``athena reindex`` rebuilds from JSONL.
"""
