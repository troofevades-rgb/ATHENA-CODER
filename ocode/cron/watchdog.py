"""Watchdog mode — fixed shell script invocation, no LLM.

The full implementation lands in Prompt 6.2; this stub exists so the
scheduler can resolve the runner symbol at import time during Prompt 6.1
tests.
"""
from __future__ import annotations


def run_watchdog_job_by_id(job_id: str) -> None:  # pragma: no cover — stub
    """Run a watchdog job by ID. Real implementation in Prompt 6.2."""
    raise NotImplementedError(
        "watchdog runner is not implemented until Prompt 6.2"
    )
