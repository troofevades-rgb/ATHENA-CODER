"""Benchmark suite runner with baseline comparison.

Discovers every module under ``scripts/bench/*.py`` that exports a
``run()`` function, executes them in order, and writes the results
to ``tests/fixtures/benchmarks/results/<timestamp>.json``.

Two modes:

- ``--baseline``: write the latest results as the baseline (used by
  CI to detect regressions over time).
- ``--compare-to <path>``: diff the latest results against a
  baseline file; exit non-zero when any benchmark regresses by more
  than the configured threshold (default 10%).

The runner is intentionally simple — no fancy scheduling, no
parallelism, no isolation beyond what each bench provides itself.
The goal is "did this PR regress something obvious?", not "what's
the absolute throughput of the system?".

Usage::

    python scripts/bench/runner.py                  # run + write
    python scripts/bench/runner.py --baseline       # mark as baseline
    python scripts/bench/runner.py \\
        --compare-to tests/fixtures/benchmarks/baselines/main.json
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Ensure the repo root is on sys.path when this script runs directly
# (``python scripts/bench/runner.py``) — pytest already does this,
# but standalone invocation isn't aware of the package layout.
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("athena.bench")


_BENCH_DIR = Path(__file__).parent
_RESULTS_DIR = (
    Path(__file__).parent.parent.parent
    / "tests" / "fixtures" / "benchmarks" / "results"
)
_DEFAULT_BASELINE = (
    Path(__file__).parent.parent.parent
    / "tests" / "fixtures" / "benchmarks" / "baselines" / "main.json"
)
_REGRESSION_THRESHOLD = 0.10  # 10% — match the design doc


# ---- discovery ----------------------------------------------------


def discover_benches() -> list[tuple[str, Callable[[], dict[str, Any]]]]:
    """Find every ``scripts/bench/<name>.py`` (excluding __init__.py
    and runner.py) that exports ``run() -> dict``.

    Returns ``[(name, run_callable), ...]`` sorted by name for
    deterministic execution order.
    """
    out: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    for py_file in sorted(_BENCH_DIR.glob("*.py")):
        if py_file.name in ("__init__.py", "runner.py"):
            continue
        module_name = f"athena_bench.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            logger.warning("skipping %s: failed to load spec", py_file)
            continue
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("skipping %s: import failed", py_file)
            continue
        run_fn = getattr(module, "run", None)
        if not callable(run_fn):
            logger.debug(
                "skipping %s: no callable run() exported", py_file,
            )
            continue
        out.append((py_file.stem, run_fn))
    return out


# ---- execution ---------------------------------------------------


def run_suite() -> dict[str, Any]:
    """Run every discovered bench, collect results."""
    started_at = datetime.now(timezone.utc).isoformat()
    suite_start = time.perf_counter()
    results: list[dict[str, Any]] = []
    for name, run_fn in discover_benches():
        bench_start = time.perf_counter()
        try:
            result = run_fn()
            elapsed_ms = (time.perf_counter() - bench_start) * 1000.0
            result.setdefault("name", name)
            result["wall_clock_ms"] = elapsed_ms
            result["status"] = "ok"
            results.append(result)
            logger.info(
                "[%s] %.2f ms (%s=%s)",
                name, elapsed_ms,
                result.get("metric", "?"), result.get("value", "?"),
            )
        except Exception as e:
            logger.exception("[%s] failed", name)
            results.append({
                "name": name,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })
    return {
        "started_at": started_at,
        "duration_ms": (time.perf_counter() - suite_start) * 1000.0,
        "results": results,
    }


def write_results(suite: dict[str, Any], *, output: Path | None = None) -> Path:
    """Persist a suite result. Returns the path written."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        output = _RESULTS_DIR / f"{stamp}.json"
    output.write_text(
        json.dumps(suite, indent=2, default=str), encoding="utf-8",
    )
    return output


def write_baseline(suite: dict[str, Any], *, target: Path | None = None) -> Path:
    """Save ``suite`` as the canonical baseline. Subsequent runs
    compare against this file unless ``--compare-to`` overrides."""
    target = target or _DEFAULT_BASELINE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(suite, indent=2, default=str), encoding="utf-8",
    )
    return target


# ---- comparison --------------------------------------------------


def compare_to_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    threshold: float = _REGRESSION_THRESHOLD,
) -> dict[str, Any]:
    """Diff each bench's ``value`` against the baseline.

    Returns ``{regressions: [...], improvements: [...], missing: [...],
    threshold: pct}`` where each list entry is
    ``{name, baseline, current, delta_pct}``.

    A regression is when ``current > baseline * (1 + threshold)``.
    The baseline metric direction matters — for latency benches, up
    is bad; for throughput benches, down is bad. We treat every
    metric as "lower is better" because all current benches measure
    latency. When/if a throughput bench lands, this function should
    grow a ``direction`` flag and the regression test should flip
    accordingly.
    """
    baseline_by_name = {
        r["name"]: r for r in baseline.get("results", [])
        if r.get("status") == "ok"
    }
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    missing: list[str] = []
    for current_result in current.get("results", []):
        name = current_result.get("name", "?")
        if current_result.get("status") != "ok":
            continue
        b = baseline_by_name.get(name)
        if b is None:
            missing.append(name)
            continue
        cur_value = float(current_result.get("value", 0))
        base_value = float(b.get("value", 0))
        if base_value <= 0:
            continue  # avoid div-by-zero on a degenerate baseline
        delta_pct = (cur_value - base_value) / base_value
        entry = {
            "name": name,
            "metric": current_result.get("metric"),
            "baseline": base_value,
            "current": cur_value,
            "delta_pct": delta_pct,
        }
        if delta_pct > threshold:
            regressions.append(entry)
        elif delta_pct < -threshold:
            improvements.append(entry)
    return {
        "threshold": threshold,
        "regressions": regressions,
        "improvements": improvements,
        "missing": missing,
    }


def render_comparison(report: dict[str, Any]) -> str:
    """Human-readable comparison report. Returned as a string so
    callers can decide whether to print, log, or attach it to a PR
    comment."""
    lines: list[str] = []
    threshold = report["threshold"]
    regressions = report["regressions"]
    improvements = report["improvements"]
    missing = report["missing"]
    if regressions:
        lines.append(f"⚠ {len(regressions)} regression(s) > {threshold:.0%}:")
        for r in regressions:
            lines.append(
                f"  {r['name']:<32} {r['metric']:<10} "
                f"{r['baseline']:.3f} → {r['current']:.3f} "
                f"({r['delta_pct']:+.1%})"
            )
    else:
        lines.append("✓ no regressions")
    if improvements:
        lines.append("")
        lines.append(f"✓ {len(improvements)} improvement(s):")
        for i in improvements:
            lines.append(
                f"  {i['name']:<32} {i['metric']:<10} "
                f"{i['baseline']:.3f} → {i['current']:.3f} "
                f"({i['delta_pct']:+.1%})"
            )
    if missing:
        lines.append("")
        lines.append(
            f"⚠ {len(missing)} bench(es) not in baseline: "
            f"{', '.join(missing)}"
        )
    return "\n".join(lines)


# ---- CLI ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-runner")
    parser.add_argument(
        "--baseline", action="store_true",
        help="Write the result as the canonical baseline.",
    )
    parser.add_argument(
        "--compare-to",
        help="Compare against the baseline at this path. "
        "Defaults to tests/fixtures/benchmarks/baselines/main.json when set "
        "as a bare flag.",
        nargs="?", const=str(_DEFAULT_BASELINE),
    )
    parser.add_argument(
        "--threshold", type=float, default=_REGRESSION_THRESHOLD,
        help="Fractional regression threshold (default 0.10 = 10%%).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    suite = run_suite()
    output = write_results(suite)
    sys.stderr.write(f"wrote {output}\n")

    if args.baseline:
        baseline_path = write_baseline(suite)
        sys.stderr.write(f"baseline updated at {baseline_path}\n")

    if args.compare_to:
        path = Path(args.compare_to)
        if not path.exists():
            sys.stderr.write(f"error: baseline not found at {path}\n")
            return 1
        baseline = json.loads(path.read_text(encoding="utf-8"))
        report = compare_to_baseline(
            suite, baseline, threshold=args.threshold,
        )
        if args.json:
            sys.stdout.write(json.dumps(report, indent=2) + "\n")
        else:
            sys.stdout.write(render_comparison(report) + "\n")
        return 1 if report["regressions"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
