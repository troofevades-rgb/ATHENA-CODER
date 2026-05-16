"""Agent mode — full LLM-driven turn for a scheduled job.

The full implementation lands in Prompt 6.2; this stub exists so the
scheduler can resolve the runner symbol at import time during Prompt 6.1
tests.
"""
from __future__ import annotations


def run_agent_job_by_id(job_id: str) -> None:  # pragma: no cover — stub
    """Run an agent-mode job by ID. Real implementation in Prompt 6.2."""
    raise NotImplementedError(
        "agent runner is not implemented until Prompt 6.2"
    )
