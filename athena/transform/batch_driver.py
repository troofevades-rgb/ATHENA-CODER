"""Headless batch driver over the existing review write path (T3-05R.2).

This module is the logic layer the textual TUI sits on top of. No
UI, no input handling — just the operations a TUI needs to call:

- load the pending trajectories (via :class:`ReviewSession.pending`)
- record a single decision (writes via :func:`save_label`)
- batch-apply one decision to multiple selected trajectories
- undo the most recent decision
- track the per-key state in memory so undo and skip-revisit work

The sidecar file layout matches what ``default_prompt`` writes
today (flat dict ``"<turn_start>-<turn_end>" → Label`` under
``<profile_dir>/labels/<session_id>.json``); we go through the
existing ``save_label`` helper rather than minting our own writer.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from pathlib import Path

from ..sessions.store import SessionMeta
from .classifier import Label, Trajectory
from .review import ReviewSession, _trajectory_key, load_labels, save_label


@dataclasses.dataclass
class BatchItem:
    """One pending trajectory + its session metadata, materialised
    once at startup so the TUI can navigate / select / re-render
    without re-walking the session store."""

    meta: SessionMeta
    trajectory: Trajectory

    @property
    def key(self) -> str:
        return _trajectory_key(self.trajectory)

    @property
    def session_id(self) -> str:
        return self.meta.session_id

    @property
    def suggestion(self) -> Label:
        """The auto-classify suggestion already attached by
        :meth:`ReviewSession.pending`."""
        return self.trajectory.auto_label


@dataclasses.dataclass
class _UndoEntry:
    item_index: int
    previous_label: Label  # the label as it was on disk before our write
    new_label: Label  # what we wrote


class BatchReviewDriver:
    """Operate over a materialised list of pending trajectories.

    The driver is stateful in memory (current_index, selection set,
    undo stack); persistence still flows through
    :func:`athena.transform.review.save_label`. Tests can drive it
    with scripted decisions; the TUI binds keys to its methods.
    """

    def __init__(
        self,
        items: Iterable[BatchItem],
        *,
        profile_dir: Path,
    ):
        self.items: list[BatchItem] = list(items)
        self.profile_dir = Path(profile_dir)
        self.current_index: int = 0
        self.selected: set[int] = set()
        self._undo_stack: list[_UndoEntry] = []
        self._max_undo: int = 50

    # ---- Loading ----

    @classmethod
    def from_review_session(
        cls,
        session: ReviewSession,
        *,
        profile_dir: Path | None = None,
    ) -> BatchReviewDriver:
        """Materialise every pending trajectory by exhausting
        :meth:`ReviewSession.pending`. ``auto_label`` is already set
        by that iterator before we see the trajectory."""
        items = [BatchItem(meta=m, trajectory=t) for m, t in session.pending()]
        return cls(items, profile_dir=profile_dir or session.profile_dir)

    # ---- Selection ----

    def toggle_select(self, index: int | None = None) -> None:
        idx = self.current_index if index is None else index
        if idx in self.selected:
            self.selected.remove(idx)
        else:
            self.selected.add(idx)

    def clear_selection(self) -> None:
        self.selected.clear()

    # ---- Navigation ----

    def go_forward(self) -> int:
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
        return self.current_index

    def go_back(self) -> int:
        if self.current_index > 0:
            self.current_index -= 1
        return self.current_index

    def jump_to(self, index: int) -> int:
        if 0 <= index < len(self.items):
            self.current_index = index
        return self.current_index

    # ---- Label persistence ----

    def label_current(self, label: Label, *, advance: bool = True) -> None:
        """Persist ``label`` for the current trajectory.

        ``"skip"`` and ``"unreviewed"`` are NOT persisted (per
        ``review.save_label`` semantics): skip just advances without
        writing; unreviewed is a quit-sentinel the TUI handles
        separately. Both still push an undo entry so the user can
        navigate-back-and-relabel cleanly.
        """
        if not self.items:
            return
        item = self.items[self.current_index]
        prev = self._current_persisted_label(item)
        self._undo_stack.append(
            _UndoEntry(
                item_index=self.current_index,
                previous_label=prev,
                new_label=label,
            )
        )
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)

        if label not in ("skip", "unreviewed"):
            save_label(self.profile_dir, item.session_id, item.key, label)
            # Reflect the new label on the in-memory trajectory so the
            # TUI's re-render sees the user_label immediately.
            item.trajectory.user_label = label
        elif label == "skip":
            item.trajectory.user_label = "unreviewed"

        if advance:
            self.go_forward()

    def batch_label(self, label: Label) -> int:
        """Apply ``label`` to every trajectory currently in
        :attr:`selected`. Returns the count of items written.

        Each item gets its own undo entry so a single Ctrl+Z
        reverses the last write; the selection itself is cleared
        on completion."""
        if not self.selected:
            return 0
        applied = 0
        for idx in sorted(self.selected):
            item = self.items[idx]
            prev = self._current_persisted_label(item)
            self._undo_stack.append(
                _UndoEntry(
                    item_index=idx,
                    previous_label=prev,
                    new_label=label,
                )
            )
            if label not in ("skip", "unreviewed"):
                save_label(self.profile_dir, item.session_id, item.key, label)
                item.trajectory.user_label = label
            elif label == "skip":
                item.trajectory.user_label = "unreviewed"
            applied += 1
        while len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        self.clear_selection()
        # Advance past the highest-indexed selection so the user
        # doesn't have to step through what they just labelled.
        if applied:
            highest = max(self._iter_recent_undo_indices(applied))
            self.current_index = min(highest + 1, len(self.items) - 1)
        return applied

    def undo(self) -> _UndoEntry | None:
        """Reverse the most recent label write. Returns the popped
        entry so the TUI can re-focus the trajectory it reverted."""
        if not self._undo_stack:
            return None
        entry = self._undo_stack.pop()
        item = self.items[entry.item_index]
        if entry.previous_label == "unreviewed":
            # Was unlabelled before — strip the key from the sidecar.
            self._delete_key(item.session_id, item.key)
            item.trajectory.user_label = "unreviewed"
        else:
            save_label(
                self.profile_dir,
                item.session_id,
                item.key,
                entry.previous_label,
            )
            item.trajectory.user_label = entry.previous_label
        self.current_index = entry.item_index
        return entry

    # ---- Introspection (test affordances + TUI status) ----

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def progress(self) -> tuple[int, int]:
        """``(labelled, total)`` — labelled excludes skips."""
        return (
            sum(1 for it in self.items if it.trajectory.user_label not in ("unreviewed",)),
            len(self.items),
        )

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def current(self) -> BatchItem | None:
        if not self.items:
            return None
        return self.items[self.current_index]

    # ---- Internals ----

    def _current_persisted_label(self, item: BatchItem) -> Label:
        existing = load_labels(self.profile_dir, item.session_id)
        return existing.get(item.key, "unreviewed")  # type: ignore[return-value]

    def _delete_key(self, session_id: str, key: str) -> None:
        """Remove one key from a session's labels sidecar. Idempotent
        — a missing key is a no-op. Goes through the same
        load/serialise/write path as :func:`save_label` so the
        formatting stays stable."""
        import json as _json

        from .review import labels_path

        path = labels_path(self.profile_dir, session_id)
        if not path.exists():
            return
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            return
        if not isinstance(data, dict) or key not in data:
            return
        del data[key]
        path.write_text(
            _json.dumps(data, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _iter_recent_undo_indices(self, count: int) -> Iterable[int]:
        return (e.item_index for e in self._undo_stack[-count:])
