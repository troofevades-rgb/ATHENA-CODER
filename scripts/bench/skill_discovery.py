"""Skill discovery latency against a synthetic fixture.

Times :func:`athena.skills.discovery.discover_skills` against 100
synthetic skill directories. The number is a proxy for how the
catalog walks at startup — a regression here means session-init got
slower.

The fixture is materialized into a tempdir each run so we don't
accidentally point at the user's real skill library and report
their times as athena's.
"""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path
from typing import Any


_SKILL_COUNT = 100


_SKILL_TEMPLATE = """---
name: {name}
description: a synthetic benchmark skill
state: active
write_origin: foreground
---

# {name}

This skill exists to populate the bench fixture. It does not do
anything meaningful. Skills with broader bodies might walk slower
or faster; this is a lower-bound on real-world content size.
"""


def _materialize_fixture(root: Path, count: int) -> None:
    skills_root = root / ".athena" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        name = f"bench-skill-{i:03d}"
        skill_dir = skills_root / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            _SKILL_TEMPLATE.format(name=name),
            encoding="utf-8",
        )


def run() -> dict[str, Any]:
    """Time discover_skills over a synthetic fixture.

    20 runs after warm-up; reports mean + p95 in milliseconds.
    """
    from athena.skills.discovery import discover_skills

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        _materialize_fixture(workspace, _SKILL_COUNT)

        # Warm — the first walk pays a frontmatter-parser import cost.
        discover_skills(workspace)

        timings_ms: list[float] = []
        for _ in range(20):
            start = time.perf_counter()
            discover_skills(workspace)
            timings_ms.append((time.perf_counter() - start) * 1000.0)

    timings_ms.sort()
    return {
        "name": "skill_discovery",
        "metric": "mean_ms",
        "unit": "ms",
        "value": statistics.mean(timings_ms),
        "p50_ms": timings_ms[len(timings_ms) // 2],
        "p95_ms": timings_ms[int(len(timings_ms) * 0.95)],
        "mean_ms": statistics.mean(timings_ms),
        "max_ms": timings_ms[-1],
        "skill_count": _SKILL_COUNT,
        "samples": len(timings_ms),
    }
