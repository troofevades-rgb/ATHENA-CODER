"""Tests for athena.transform.batch_driver (T3-05R.2).

The driver writes through ``review.save_label``, so the sidecar
file ends up byte-identical to what ``default_prompt`` produces.
Tests assert on the on-disk file (the canonical contract) rather
than driver internals.
"""

from __future__ import annotations

import json
from pathlib import Path

from athena.transform.batch_driver import BatchItem, BatchReviewDriver
from athena.transform.classifier import Trajectory
from athena.transform.review import labels_path, load_labels


def _meta(session_id: str = "s1"):
    """Lightweight SessionMeta stub. Only ``session_id`` is read by
    the driver."""

    from athena.sessions.store import SessionMeta

    return SessionMeta(
        session_id=session_id,
        profile="default",
        model="m",
        workspace="/tmp",
    )


def _traj(
    session_id: str = "s1",
    start: int = 0,
    end: int = 2,
    auto: str = "unreviewed",
) -> Trajectory:
    return Trajectory(
        session_id=session_id,
        turn_start=start,
        turn_end=end,
        turns=[
            {"role": "user", "content": "do thing"},
            {"role": "assistant", "content": "did thing"},
        ],
        auto_label=auto,  # type: ignore[arg-type]
    )


def _item(traj: Trajectory) -> BatchItem:
    return BatchItem(meta=_meta(traj.session_id), trajectory=traj)


# ---------------------------------------------------------------------------
# Single label
# ---------------------------------------------------------------------------


def test_single_label_persists_sidecar_format(tmp_path: Path) -> None:
    driver = BatchReviewDriver(
        [_item(_traj(start=0, end=2)), _item(_traj(start=3, end=5))],
        profile_dir=tmp_path,
    )
    driver.label_current("good")

    sidecar = labels_path(tmp_path, "s1")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    # Flat dict, "<start>-<end>" → Label literal — the existing contract.
    assert data == {"0-2": "good"}
    # And advance happened.
    assert driver.current_index == 1


def test_single_label_skip_does_not_persist(tmp_path: Path) -> None:
    driver = BatchReviewDriver(
        [_item(_traj(start=0, end=2))],
        profile_dir=tmp_path,
    )
    driver.label_current("skip")
    sidecar = labels_path(tmp_path, "s1")
    # Skip never writes to disk.
    assert not sidecar.exists() or load_labels(tmp_path, "s1") == {}


def test_single_label_preference_pair_persists(tmp_path: Path) -> None:
    driver = BatchReviewDriver(
        [_item(_traj(start=0, end=2))],
        profile_dir=tmp_path,
    )
    driver.label_current("preference_pair")
    assert load_labels(tmp_path, "s1") == {"0-2": "preference_pair"}


# ---------------------------------------------------------------------------
# Batch label
# ---------------------------------------------------------------------------


def test_batch_label_applies_to_selection(tmp_path: Path) -> None:
    items = [
        _item(_traj(start=0, end=2)),
        _item(_traj(start=3, end=5)),
        _item(_traj(start=6, end=8)),
    ]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.toggle_select(0)
    driver.toggle_select(2)
    written = driver.batch_label("good")
    assert written == 2

    data = load_labels(tmp_path, "s1")
    assert data == {"0-2": "good", "6-8": "good"}
    # Middle (unselected) item stays unreviewed on disk.
    assert "3-5" not in data
    # Selection gets cleared.
    assert driver.selected == set()


def test_batch_label_advances_past_highest_selected(tmp_path: Path) -> None:
    items = [
        _item(_traj(start=0, end=2)),
        _item(_traj(start=3, end=5)),
        _item(_traj(start=6, end=8)),
    ]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.toggle_select(0)
    driver.toggle_select(1)
    driver.batch_label("good")
    # Highest selected was index 1, so current should land at index 2.
    assert driver.current_index == 2


def test_batch_label_empty_selection_writes_nothing(tmp_path: Path) -> None:
    driver = BatchReviewDriver([_item(_traj())], profile_dir=tmp_path)
    assert driver.batch_label("good") == 0
    assert load_labels(tmp_path, "s1") == {}


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def test_undo_reverts_last_decision(tmp_path: Path) -> None:
    items = [_item(_traj(start=0, end=2)), _item(_traj(start=3, end=5))]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.label_current("good")
    assert load_labels(tmp_path, "s1") == {"0-2": "good"}
    # Now undo — the key should disappear from the sidecar.
    entry = driver.undo()
    assert entry is not None
    assert entry.new_label == "good"
    assert entry.previous_label == "unreviewed"
    assert load_labels(tmp_path, "s1") == {}
    # Cursor lands back on the trajectory we just unlabelled.
    assert driver.current_index == 0
    assert items[0].trajectory.user_label == "unreviewed"


def test_undo_with_no_history_returns_none(tmp_path: Path) -> None:
    driver = BatchReviewDriver([_item(_traj())], profile_dir=tmp_path)
    assert driver.undo() is None


def test_undo_restores_previous_label_when_relabelling(tmp_path: Path) -> None:
    """If the trajectory was already labelled 'bad' on disk, undoing
    a subsequent 'good' restores 'bad' (not 'unreviewed')."""
    driver = BatchReviewDriver([_item(_traj(start=0, end=2))], profile_dir=tmp_path)
    driver.label_current("bad", advance=False)
    driver.label_current("good", advance=False)
    assert load_labels(tmp_path, "s1") == {"0-2": "good"}
    driver.undo()
    assert load_labels(tmp_path, "s1") == {"0-2": "bad"}


def test_undo_works_after_batch(tmp_path: Path) -> None:
    items = [
        _item(_traj(start=0, end=2)),
        _item(_traj(start=3, end=5)),
    ]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.toggle_select(0)
    driver.toggle_select(1)
    driver.batch_label("good")
    assert load_labels(tmp_path, "s1") == {"0-2": "good", "3-5": "good"}
    # Each batch item lands as a separate undo entry — one Ctrl+Z
    # pops just the last write.
    driver.undo()
    assert load_labels(tmp_path, "s1") == {"0-2": "good"}


# ---------------------------------------------------------------------------
# Skip / suggestion
# ---------------------------------------------------------------------------


def test_skip_leaves_unreviewed(tmp_path: Path) -> None:
    items = [_item(_traj(start=0, end=2)), _item(_traj(start=3, end=5))]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.label_current("skip")
    # Skip never writes; the sidecar stays empty.
    assert load_labels(tmp_path, "s1") == {}
    # And advance still happens so the user moves on.
    assert driver.current_index == 1


def test_auto_suggestion_attached_from_classifier(tmp_path: Path) -> None:
    """The driver doesn't re-classify; it surfaces whatever
    ``auto_label`` was set by ReviewSession.pending."""
    items = [_item(_traj(start=0, end=2, auto="good"))]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    assert driver.current().suggestion == "good"


# ---------------------------------------------------------------------------
# Multi-session sidecar isolation
# ---------------------------------------------------------------------------


def test_multi_session_writes_isolated(tmp_path: Path) -> None:
    items = [
        _item(_traj(session_id="s1", start=0, end=2)),
        _item(_traj(session_id="s2", start=0, end=2)),
    ]
    driver = BatchReviewDriver(items, profile_dir=tmp_path)
    driver.label_current("good")
    driver.label_current("bad")
    assert load_labels(tmp_path, "s1") == {"0-2": "good"}
    assert load_labels(tmp_path, "s2") == {"0-2": "bad"}


# ---------------------------------------------------------------------------
# Reuse of existing default_prompt path — must stay unchanged
# ---------------------------------------------------------------------------


def test_existing_default_prompt_path_unchanged(tmp_path: Path) -> None:
    """A direct call to ``save_label`` produces the same on-disk
    format the batch driver writes. Belt-and-braces guard against
    accidental divergence."""
    from athena.transform.review import save_label

    save_label(tmp_path, "s1", "0-2", "good")
    via_helper = load_labels(tmp_path, "s1")

    driver = BatchReviewDriver(
        [_item(_traj(session_id="s1", start=4, end=6))],
        profile_dir=tmp_path,
    )
    driver.label_current("bad")
    combined = load_labels(tmp_path, "s1")

    # The helper-written entry survives the driver's write.
    assert combined == {"0-2": "good", "4-6": "bad"}
    # And the helper's format is preserved (sort_keys + indent=2).
    raw = labels_path(tmp_path, "s1").read_text(encoding="utf-8")
    assert raw == json.dumps(combined, sort_keys=True, indent=2) + "\n"
    assert via_helper == {"0-2": "good"}  # sanity from earlier call
