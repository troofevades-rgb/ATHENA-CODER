"""Tool-call dispatch latency (Read tool, 100 iterations).

Measures how long the tools.registry round-trip takes — schema
lookup, kwargs unpacking, dispatcher invocation, result formatting.
No model in the loop; this isolates the framework's own overhead so
a regression in the registry surfaces as a latency bump rather than
getting buried under provider variance.

Iterations: 100 cold + 100 hot, since the first invocation may pay
import / cache-warm costs.
"""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path
from typing import Any


def run() -> dict[str, Any]:
    """Return a single benchmark result dict.

    Reports:
    - p50 / p95 / p99 latency in milliseconds (hot-loop)
    - sample count
    """
    from athena.tools.file_ops import Read as _read_tool
    from athena.tools import file_ops

    # Build a small fixture file so every iteration reads the same
    # bytes — variation comes from the framework, not the disk.
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        file_ops.set_workspace(workspace, max_read=8192)
        fixture = workspace / "fixture.txt"
        fixture.write_text(
            "the quick brown fox jumps over the lazy dog\n" * 20,
            encoding="utf-8",
        )

        # Warm: discard 10 iterations to let any lazy import / cache
        # land before we start measuring.
        for _ in range(10):
            _read_tool(file_path="fixture.txt")

        # Measure 100 iterations.
        timings_ms: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            _read_tool(file_path="fixture.txt")
            timings_ms.append((time.perf_counter() - start) * 1000.0)

    timings_ms.sort()
    return {
        "name": "tool_call_latency",
        "metric": "p50_ms",
        "unit": "ms",
        "value": timings_ms[len(timings_ms) // 2],
        "p50_ms": timings_ms[len(timings_ms) // 2],
        "p95_ms": timings_ms[int(len(timings_ms) * 0.95)],
        "p99_ms": timings_ms[int(len(timings_ms) * 0.99)],
        "max_ms": timings_ms[-1],
        "mean_ms": statistics.mean(timings_ms),
        "samples": len(timings_ms),
    }
