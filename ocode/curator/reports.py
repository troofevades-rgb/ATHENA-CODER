"""Persist a curator run as run.json + REPORT.md.

Each run lands under ``<logs_root>/curator/<YYYYMMDD-HHMMSS>/``:

- ``run.json`` carries the structured parsed YAML, fork metadata
  (duration, stdout/stderr byte counts, error), and the dry-run flag —
  machine readable, used by ``ocode curator inspect-last`` and Phase 16
  telemetry.
- ``REPORT.md`` is a human-readable summary printed at the end of a
  foreground ``ocode curator run``.

The function returns the same shape the orchestrator hands back to
``maybe_run_curator`` callers.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_run(
    agent: Any,
    fork_result: Any,
    parsed_yaml: dict,
    *,
    dry_run: bool = False,
    logs_root: Path | None = None,
) -> dict:
    """Write run.json + REPORT.md and return the run summary dict."""
    runs = parsed_yaml.get("runs") or []
    now = datetime.now(timezone.utc)
    run_dir = (logs_root or Path.cwd()) / "curator" / now.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    decision_counts = Counter(r["decision"] for r in runs)
    targets_by_decision: dict[str, list[str]] = {}
    for r in runs:
        if r.get("target"):
            targets_by_decision.setdefault(r["decision"], []).append(r["target"])

    summary = {
        "started_at": now.isoformat(),
        "dry_run": dry_run,
        "total_skills": len(runs),
        "decision_counts": dict(decision_counts),
        "targets_by_decision": targets_by_decision,
        "decisions": runs,
        "fork": {
            "duration_s": getattr(fork_result, "duration_s", 0.0),
            "error": getattr(fork_result, "error", None),
            "child_session_id": getattr(fork_result, "child_session_id", None),
            "stdout_bytes": len(getattr(fork_result, "stdout", "") or ""),
            "stderr_bytes": len(getattr(fork_result, "stderr", "") or ""),
        },
        "report_path": str(run_dir / "REPORT.md"),
    }

    (run_dir / "run.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    (run_dir / "REPORT.md").write_text(_render_markdown(summary), encoding="utf-8")
    if getattr(fork_result, "stdout", None):
        (run_dir / "fork-stdout.log").write_text(fork_result.stdout, encoding="utf-8")
    if getattr(fork_result, "stderr", None):
        (run_dir / "fork-stderr.log").write_text(fork_result.stderr, encoding="utf-8")

    return summary


def _render_markdown(summary: dict) -> str:
    started = summary["started_at"]
    suffix = " (dry-run)" if summary["dry_run"] else ""
    lines: list[str] = [f"# Curator run {started}{suffix}", ""]
    lines.append(f"Total skills reviewed: {summary['total_skills']}")
    lines.append(f"Fork duration: {summary['fork']['duration_s']:.2f}s")
    if summary["fork"]["error"]:
        lines.append(f"Fork error: {summary['fork']['error']}")
    lines.append("")
    lines.append("## Decisions")
    if not summary["decision_counts"]:
        lines.append("(none)")
    else:
        for decision, count in sorted(summary["decision_counts"].items()):
            targets = summary["targets_by_decision"].get(decision)
            if targets:
                lines.append(f"  - {decision}: {count} ({', '.join(sorted(set(targets)))})")
            else:
                lines.append(f"  - {decision}: {count}")
    lines.append("")
    lines.append("## Per-skill decisions")
    if not summary["decisions"]:
        lines.append("(none)")
    else:
        for r in summary["decisions"]:
            target_suffix = f" → {r['target']}" if r.get("target") else ""
            lines.append(f"- **{r['skill']}**: {r['decision']}{target_suffix}")
            if r.get("rationale"):
                lines.append(f"    rationale: {r['rationale']}")
    return "\n".join(lines).rstrip() + "\n"
