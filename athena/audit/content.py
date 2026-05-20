"""Snapshot-tarball content extraction for `audit ... --content` (T3-04.1).

``MutationAuditLog`` records the file's pre-mutation state by
linking each audit row to a ``snapshot_id``. The snapshot store
holds a content-addressed ``.tar.gz`` per snapshot; the tarball
contains the file bytes as they were *before* the mutation
recorded on that row.

So a real content diff for ``event_n`` is:

  before = bytes of the target file inside ``event_n.snapshot_id``'s tarball
  after  = bytes of the same file inside the NEXT event-for-same-path's
           snapshot tarball  (because the next event's snapshot
           captures the state right before the next mutation —
           which IS the state after this mutation), OR the live
           on-disk file if no later event exists.

That's "use what the audit log points at" rather than
"reconstruct from outside the audit log" — the snapshot is part
of athena's mutation-record system; `snapshot_and_record` writes
the audit row + tarball atomically.

Public surface: :func:`extract_file_from_snapshot` and
:func:`unified_diff_for_event`.
"""

from __future__ import annotations

import difflib
import logging
import tarfile
from pathlib import Path

from ..safety.snapshots import SNAPSHOT_ROOT

logger = logging.getLogger(__name__)


def extract_file_from_snapshot(
    snapshot_id: str,
    target_path: str | Path,
    *,
    snapshot_root: Path | None = None,
    relative_to: Path | None = None,
) -> str | None:
    """Pull the captured bytes of ``target_path`` from the tarball
    written for ``snapshot_id``.

    Returns the decoded text, or ``None`` when:
    - the snapshot tarball can't be located,
    - the target file wasn't captured in that snapshot,
    - the bytes don't decode as UTF-8 (we don't try to surface
      binary content in a text diff).

    ``relative_to`` matches the SnapshotStore's ``relative_to``
    (``Path.home()`` by default); tarball member names are stored
    relative to it.
    """
    root = (snapshot_root or SNAPSHOT_ROOT).expanduser()
    if not root.exists():
        return None
    base = (relative_to or Path.home()).resolve()
    target = Path(target_path).resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        # Outside the snapshot's relative_to root — the tarball
        # stores members under the file's basename in that case
        # (see SnapshotStore._create_snapshot's fallback). Fall
        # through and try both forms.
        rel = Path(target.name)

    # Find the tarball file under the dated tree.
    tarballs = list(root.rglob(f"{snapshot_id}.tar.gz"))
    if not tarballs:
        return None
    tarball_path = tarballs[0]

    candidates = {rel.as_posix(), target.name}
    try:
        with tarfile.open(tarball_path, mode="r:gz") as tf:
            for name in candidates:
                try:
                    member = tf.getmember(name)
                except KeyError:
                    continue
                if not member.isreg():
                    continue
                handle = tf.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    return None
    except (tarfile.TarError, OSError) as e:
        logger.debug("snapshot extract failed for %s: %s", snapshot_id, e)
        return None
    return None


def unified_diff_for_event(
    *,
    snapshot_id: str | None,
    next_snapshot_id: str | None,
    target_path: str,
    snapshot_root: Path | None = None,
    relative_to: Path | None = None,
    context_lines: int = 3,
    max_lines: int = 200,
) -> str | None:
    """Build a unified diff for one mutation event.

    Returns the diff text (without a trailing newline) or ``None``
    when neither the before nor after content can be recovered.

    - ``snapshot_id`` — the audit row's own snapshot, capturing the
      file state BEFORE this mutation.
    - ``next_snapshot_id`` — the next mutation-for-same-path's
      snapshot (the AFTER state). When ``None``, falls back to the
      current on-disk file at ``target_path``.

    ``max_lines`` caps the diff output so a single huge mutation
    doesn't drown the surrounding rendering — extra lines collapse
    to a ``[diff truncated, +N lines]`` marker.
    """
    before = (
        extract_file_from_snapshot(
            snapshot_id,
            target_path,
            snapshot_root=snapshot_root,
            relative_to=relative_to,
        )
        if snapshot_id
        else None
    )

    after: str | None
    if next_snapshot_id:
        after = extract_file_from_snapshot(
            next_snapshot_id,
            target_path,
            snapshot_root=snapshot_root,
            relative_to=relative_to,
        )
    else:
        # No later event captured this file's state — read the
        # live file if it still exists. If it doesn't, the mutation
        # must have been a delete; treat after as empty.
        live = Path(target_path)
        if live.exists():
            try:
                after = live.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                after = None
        else:
            after = ""

    if before is None and after is None:
        return None
    # If the caller passed no snapshot_id and the live file is
    # missing too, there's no useful diff to produce — bail.
    if snapshot_id is None and next_snapshot_id is None and not Path(target_path).exists():
        return None

    before = before or ""
    after = after or ""
    if before == after:
        return ""

    diff_iter = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
        n=context_lines,
    )
    lines = list(diff_iter)
    if not lines:
        return ""
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        remaining = len(lines) - max_lines
        kept.append(f"[diff truncated, +{remaining} more lines]")
        lines = kept
    return "\n".join(lines)
