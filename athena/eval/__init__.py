"""Eval battery (T7-03).

Closes the trio T7-01 / T7-02 / T7-03: T7-01 headless primitive
(`athena -p ... --json`), T7-02 batch runner (`athena batch`),
T7-03 eval battery (`athena eval`).

Composition: an eval IS a batch with a scoring pass at the
end. Each case carries an ``expected`` field + an optional
per-case ``scorer``; after batch_run completes, every per-run
envelope is loaded, the assistant_text scored against expected,
results aggregated into an EvalSummary.

Public surface:

  :func:`athena.eval.runner.run_eval` — execute an eval
  :class:`athena.eval.summary.EvalCase` — one labeled case
  :class:`athena.eval.summary.EvalScore` — one scored result
  :class:`athena.eval.summary.EvalSummary` — aggregated outcome
  :mod:`athena.eval.scorers`             — Protocol + built-ins
"""

from __future__ import annotations

from .scorers import (
    Score,
    Scorer,
    get_scorer,
    register_scorer,
    list_scorers,
)
from .summary import EvalCase, EvalScore, EvalSummary

__all__ = [
    "EvalCase",
    "EvalScore",
    "EvalSummary",
    "Score",
    "Scorer",
    "get_scorer",
    "register_scorer",
    "list_scorers",
]
