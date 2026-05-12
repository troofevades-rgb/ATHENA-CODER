"""Copy Hermes session transcripts to ocode v2's per-profile sessions dir.

Each Hermes session is a ``<source>/sessions/<session_id>.jsonl`` file. The
first line is an optional metadata header (``{"_meta": {...}}``); subsequent
lines are individual messages. The importer:

1. Copies the message stream to ``<dest>/profiles/<profile>/sessions/<id>.jsonl``
2. Writes a sidecar ``<id>.meta.json`` extracted from the header (model,
   started_at, ended_at, workspace, tags). Filename metadata wins when the
   header is absent.
3. Preserves message order exactly.

Phase 2 will plumb FTS5 indexing on these files; for now we only need them
on disk.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .report import Report


_FILENAME_TS_RE = re.compile(r"^(?P<id>[^_]+?)(?:_(?P<ts>\d{8,}))?$")


def _read_meta_header(first_line: str) -> dict[str, Any]:
    try:
        obj = json.loads(first_line)
    except json.JSONDecodeError:
        return {}
    if isinstance(obj, dict) and "_meta" in obj and isinstance(obj["_meta"], dict):
        return obj["_meta"]
    return {}


def _ingest_session(
    src_file: Path,
    dest_dir: Path,
    *,
    report: Report,
    profile: str,
    dry_run: bool,
) -> None:
    session_id = src_file.stem
    lines = src_file.read_text(encoding="utf-8").splitlines()
    if not lines:
        report.add("session_skipped_empty", {"source": str(src_file)})
        return

    meta = _read_meta_header(lines[0])
    body_lines = lines[1:] if meta else lines

    if not meta:
        # Fall back to filename + mtime.
        m = _FILENAME_TS_RE.match(session_id)
        if m and m.group("ts"):
            ts = int(m.group("ts"))
            meta = {
                "session_id": m.group("id"),
                "started_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            }
        else:
            stat_started = datetime.fromtimestamp(
                src_file.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            meta = {"session_id": session_id, "started_at": stat_started}

    meta = dict(meta)
    meta.setdefault("session_id", session_id)
    meta.setdefault("profile", profile)
    meta.setdefault("imported_at", datetime.now(timezone.utc).isoformat())
    meta["source"] = str(src_file)

    out_jsonl = dest_dir / f"{session_id}.jsonl"
    out_meta = dest_dir / f"{session_id}.meta.json"

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_jsonl.write_text("\n".join(body_lines) + ("\n" if body_lines else ""), encoding="utf-8")
        out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    report.add("imported_session", {
        "session_id": session_id,
        "source": str(src_file),
        "destination": str(out_jsonl),
        "message_count": len(body_lines),
        "dry_run": dry_run,
    })


def import_sessions(
    source: Path,
    dest: Path,
    *,
    profile: str = "default",
    report: Report,
    dry_run: bool = False,
) -> None:
    sessions_src = source / "sessions"
    if not sessions_src.exists():
        report.add("sessions_warning", {
            "reason": "no_sessions_dir",
            "path": str(sessions_src),
        })
        return

    target_dir = dest / "profiles" / profile / "sessions"
    for jsonl in sorted(sessions_src.glob("*.jsonl")):
        _ingest_session(jsonl, target_dir, report=report, profile=profile, dry_run=dry_run)
