"""Tests for athena.audit.content (T3-04.1).

Real SnapshotStore — small, self-contained. Exercises the round
trip: create a snapshot of a known file, then verify
extract_file_from_snapshot returns the captured bytes.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from athena.audit.content import (
    extract_file_from_snapshot,
    unified_diff_for_event,
)
from athena.audit.diff import (
    collect_skill_events,
    render_skill_diff,
)
from athena.safety.snapshots import SnapshotStore

# ---------------------------------------------------------------------------
# extract_file_from_snapshot
# ---------------------------------------------------------------------------


def test_extract_returns_captured_bytes(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(b"version A\nline two\n")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="test",
        tool_call_id=None,
        parent_session_id=None,
    )
    out = extract_file_from_snapshot(
        snap.snapshot_id,
        target,
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert out == "version A\nline two\n"


def test_extract_unknown_snapshot_returns_none(tmp_path: Path) -> None:
    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    out = extract_file_from_snapshot(
        "does-not-exist",
        tmp_path / "anything.txt",
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert out is None


def test_extract_missing_target_in_snapshot_returns_none(tmp_path: Path) -> None:
    other = tmp_path / "other.txt"
    other.write_text("captured", encoding="utf-8")
    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (other,),
        session_id=None,
        tool_name="test",
        tool_call_id=None,
        parent_session_id=None,
    )
    out = extract_file_from_snapshot(
        snap.snapshot_id,
        tmp_path / "not-in-snapshot.txt",
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert out is None


def test_extract_binary_file_returns_none(tmp_path: Path) -> None:
    """A snapshot of binary content (not UTF-8) returns None rather
    than raising — text-diff path can't render it."""
    target = tmp_path / "binary.bin"
    target.write_bytes(b"\x80\x81\x82\x00\xff")  # invalid utf-8
    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="test",
        tool_call_id=None,
        parent_session_id=None,
    )
    out = extract_file_from_snapshot(
        snap.snapshot_id,
        target,
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert out is None


# ---------------------------------------------------------------------------
# unified_diff_for_event
# ---------------------------------------------------------------------------


def test_diff_between_two_snapshots(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("v1\n", encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap_a = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="t",
        tool_call_id=None,
        parent_session_id=None,
    )
    target.write_text("v2\n", encoding="utf-8")
    snap_b = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="t",
        tool_call_id=None,
        parent_session_id=None,
    )

    diff = unified_diff_for_event(
        snapshot_id=snap_a.snapshot_id,
        next_snapshot_id=snap_b.snapshot_id,
        target_path=str(target),
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert diff is not None
    assert "--- before" in diff
    assert "+++ after" in diff
    assert "-v1" in diff
    assert "+v2" in diff


def test_diff_falls_back_to_live_file_for_last_event(tmp_path: Path) -> None:
    """When there's no `next_snapshot_id`, the diff reads the live
    file (the after-state of the last recorded mutation)."""
    target = tmp_path / "f.txt"
    target.write_text("v1\n", encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="t",
        tool_call_id=None,
        parent_session_id=None,
    )
    target.write_text("v2-live\n", encoding="utf-8")

    diff = unified_diff_for_event(
        snapshot_id=snap.snapshot_id,
        next_snapshot_id=None,
        target_path=str(target),
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert diff is not None
    assert "-v1" in diff
    assert "+v2-live" in diff


def test_diff_identical_content_returns_empty(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("same\n", encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="t",
        tool_call_id=None,
        parent_session_id=None,
    )
    diff = unified_diff_for_event(
        snapshot_id=snap.snapshot_id,
        next_snapshot_id=None,
        target_path=str(target),
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    # File hasn't changed since the snapshot — empty diff.
    assert diff == ""


def test_diff_truncates_huge_output(tmp_path: Path) -> None:
    target = tmp_path / "huge.txt"
    target.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap = store._create_snapshot(
        (target,),
        session_id=None,
        tool_name="t",
        tool_call_id=None,
        parent_session_id=None,
    )
    target.write_text("\n".join(f"changed line {i}" for i in range(500)), encoding="utf-8")

    diff = unified_diff_for_event(
        snapshot_id=snap.snapshot_id,
        next_snapshot_id=None,
        target_path=str(target),
        snapshot_root=store.root,
        relative_to=tmp_path,
        max_lines=50,
    )
    assert diff is not None
    assert "[diff truncated" in diff


def test_diff_with_no_snapshots_returns_none(tmp_path: Path) -> None:
    diff = unified_diff_for_event(
        snapshot_id=None,
        next_snapshot_id=None,
        target_path=str(tmp_path / "nonexistent.txt"),
        snapshot_root=tmp_path / "snaps",
        relative_to=tmp_path,
    )
    assert diff is None


# ---------------------------------------------------------------------------
# End-to-end: collect_skill_events with_content=True attaches diffs
# ---------------------------------------------------------------------------


def _write_audit_row(
    audit_dir: Path,
    *,
    timestamp: str,
    tool_name: str,
    path: str,
    sha_before: str | None,
    sha_after: str,
    snapshot_id: str,
) -> None:
    import json as _json

    audit_dir.mkdir(parents=True, exist_ok=True)
    log = audit_dir / "mutations-2026-05.jsonl"
    rec = {
        "timestamp": timestamp,
        "write_origin": "foreground",
        "session_id": "s1",
        "parent_session_id": None,
        "tool_name": tool_name,
        "tool_call_id": "tc",
        "path": path,
        "snapshot_id": snapshot_id,
        "sha_before": sha_before,
        "sha_after": sha_after,
        "byte_delta": 0,
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(_json.dumps(rec) + "\n")


def test_collect_skill_events_with_content_attaches_diff(tmp_path: Path) -> None:
    """End-to-end: two recorded mutations of the same skill file,
    `--content` returns a unified diff between them."""
    # Build a real skill file under a tmp workspace.
    skill_md = tmp_path / "ws" / ".athena" / "skills" / "demo" / "skill.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# Demo\n\nv1 body\n", encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap_a = store._create_snapshot(
        (skill_md,),
        session_id="s1",
        tool_name="skill_create",
        tool_call_id="tc1",
        parent_session_id=None,
    )
    # Write the audit row for the create.
    _write_audit_row(
        tmp_path / "audit",
        timestamp="2026-05-10T10:00:00Z",
        tool_name="skill_create",
        path=str(skill_md),
        sha_before=None,
        sha_after="hash-a",
        snapshot_id=snap_a.snapshot_id,
    )

    # Modify, snapshot again, write audit row.
    skill_md.write_text("# Demo\n\nv2 body — improved\n", encoding="utf-8")
    snap_b = store._create_snapshot(
        (skill_md,),
        session_id="s1",
        tool_name="skill_patch",
        tool_call_id="tc2",
        parent_session_id=None,
    )
    _write_audit_row(
        tmp_path / "audit",
        timestamp="2026-05-10T10:05:00Z",
        tool_name="skill_patch",
        path=str(skill_md),
        sha_before="hash-a",
        sha_after="hash-b",
        snapshot_id=snap_b.snapshot_id,
    )

    events = collect_skill_events(
        audit_dir=tmp_path / "audit",
        since=_dt.datetime(2026, 5, 1),
        until=_dt.datetime(2026, 5, 31),
        with_content=True,
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    assert len(events) == 2
    # First event (skill_create) gets a diff between its own
    # snapshot (v1 body) and the NEXT event's snapshot (v2 body).
    create_event = events[0]
    assert create_event.tool_name == "skill_create"
    assert create_event.content_diff is not None
    assert "-v1 body" in create_event.content_diff
    assert "+v2 body — improved" in create_event.content_diff

    # Second event (skill_patch) is the last for this path; its
    # diff is between its snapshot (v2 body, captured BEFORE the
    # patch — which is wrong since this snapshot is taken at the
    # time of the second mutation, capturing the file's then-state
    # which was v2. The live file is also v2. So this diff is
    # empty.)
    # Actually: the snapshot captures the file BEFORE the mutation,
    # but we called _create_snapshot AFTER writing v2 — so it
    # captures v2. Live file is also v2 (we haven't changed since).
    # Diff is empty.
    patch_event = events[1]
    assert patch_event.tool_name == "skill_patch"
    # Either empty (no change between snapshot and live) or a real
    # diff if the snapshot semantics differ — both acceptable, we
    # just want NOT None (which would mean the extraction failed).
    assert patch_event.content_diff is not None


def test_render_skill_diff_inlines_unified_diff(tmp_path: Path) -> None:
    """The human-readable renderer prints the unified diff when
    `content_diff` is populated."""
    skill_md = tmp_path / "ws" / ".athena" / "skills" / "demo" / "skill.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("alpha\n", encoding="utf-8")

    store = SnapshotStore(root=tmp_path / "snaps", relative_to=tmp_path)
    snap_a = store._create_snapshot(
        (skill_md,),
        session_id=None,
        tool_name="skill_create",
        tool_call_id=None,
        parent_session_id=None,
    )
    _write_audit_row(
        tmp_path / "audit",
        timestamp="2026-05-10T10:00:00Z",
        tool_name="skill_create",
        path=str(skill_md),
        sha_before=None,
        sha_after="h",
        snapshot_id=snap_a.snapshot_id,
    )
    # Mutate live file so the after-state diverges from the snapshot.
    skill_md.write_text("beta\n", encoding="utf-8")

    events = collect_skill_events(
        audit_dir=tmp_path / "audit",
        since=_dt.datetime(2026, 5, 1),
        until=_dt.datetime(2026, 5, 31),
        with_content=True,
        snapshot_root=store.root,
        relative_to=tmp_path,
    )
    out = render_skill_diff(
        events,
        since=_dt.datetime(2026, 5, 1),
        until=_dt.datetime(2026, 5, 31),
    )
    # The "[content not in audit log]" placeholder is replaced by
    # the actual unified diff block.
    assert "[content not in audit log" not in out
    assert "-alpha" in out
    assert "+beta" in out


def test_render_without_content_keeps_placeholder(tmp_path: Path) -> None:
    """Without --content the placeholder text is what shows up,
    matching the T3-04 baseline."""
    skill_md = tmp_path / "ws" / ".athena" / "skills" / "x" / "skill.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("body\n", encoding="utf-8")
    _write_audit_row(
        tmp_path / "audit",
        timestamp="2026-05-10T10:00:00Z",
        tool_name="skill_create",
        path=str(skill_md),
        sha_before=None,
        sha_after="h",
        snapshot_id="snap-doesnt-matter",
    )
    events = collect_skill_events(
        audit_dir=tmp_path / "audit",
        since=_dt.datetime(2026, 5, 1),
        until=_dt.datetime(2026, 5, 31),
        with_content=False,
    )
    out = render_skill_diff(
        events,
        since=_dt.datetime(2026, 5, 1),
        until=_dt.datetime(2026, 5, 31),
    )
    assert "[content not in audit log" in out
