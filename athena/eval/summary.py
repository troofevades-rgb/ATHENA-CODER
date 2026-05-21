"""Eval input + output data shapes (T7-03.1).

Mirrors the T7-02 batch shapes — one entry per case on the
input side, an aggregated summary on the output side. Both
JSON-safe so they round-trip cleanly + a downstream tool
(model-improvement gate, eval-vs-baseline differ) parses
either with stdlib only.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any


# Excerpt limits keep the summary file manageable for human
# inspection; full text lives in the per-case score row +
# per-run envelope on disk.
_ACTUAL_EXCERPT_LIMIT = 240
_DETAILS_EXCERPT_LIMIT = 200


@dataclasses.dataclass
class EvalCase:
    """One case in the input JSONL.

    Required: ``task`` (the prompt) + ``expected`` (the
    correct answer or pattern).

    Optional:
      ``case_id``   — operator-supplied stable ID; auto-minted
                      as ``e-<uuid12>`` otherwise. Used to
                      diff against a ``--baseline`` run.
      ``scorer``    — per-case scorer name override. Default
                      comes from the CLI's ``--scorer`` flag.
      ``cwd``       — workspace override (passes to batch).
      ``timeout_s`` — wall-clock cap (passes to batch).
      ``model``     — model override (passes to batch).

    Extra keys are tolerated and preserved through the batch's
    JSONL output so a custom scorer can read them off the
    case context.
    """

    task: str
    expected: Any
    case_id: str | None = None
    scorer: str | None = None
    cwd: str | None = None
    timeout_s: float | None = None
    model: str | None = None
    extras: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalCase":
        task = d.get("task")
        if not task or not str(task).strip():
            raise ValueError("EvalCase requires non-empty 'task'")
        if "expected" not in d:
            raise ValueError("EvalCase requires 'expected' field")
        known = {
            "task", "expected", "case_id", "scorer", "cwd",
            "timeout_s", "model",
        }
        extras = {k: v for k, v in d.items() if k not in known}
        return cls(
            task=str(task),
            expected=d["expected"],
            case_id=str(d["case_id"]) if d.get("case_id") else None,
            scorer=str(d["scorer"]) if d.get("scorer") else None,
            cwd=str(d["cwd"]) if d.get("cwd") else None,
            timeout_s=(
                float(d["timeout_s"]) if d.get("timeout_s") is not None else None
            ),
            model=str(d["model"]) if d.get("model") else None,
            extras=extras,
        )


def mint_case_id() -> str:
    """``e-<uuid12>`` — same family as the other ID shapes."""
    import uuid
    return f"e-{uuid.uuid4().hex[:12]}"


@dataclasses.dataclass
class EvalScore:
    """One case's scored outcome — the bridge between the
    case's expected and the model's actual."""

    case_id: str
    run_id: str
    task_excerpt: str       # short version of the prompt
    actual_excerpt: str     # short version of the model's answer
    passed: bool
    score: float
    scorer: str             # name of the scorer that ran
    details: str            # human-readable explanation
    # Status of the underlying RunResult — eval scoring is
    # only meaningful when the run completed; runs that
    # errored / timed out / were interrupted appear in the
    # summary as their own categories.
    run_status: str
    envelope_path: str      # per-run JSON envelope path

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "task_excerpt": self.task_excerpt,
            "actual_excerpt": self.actual_excerpt,
            "passed": bool(self.passed),
            "score": round(float(self.score), 4),
            "scorer": self.scorer,
            "details": self.details,
            "run_status": self.run_status,
            "envelope_path": self.envelope_path,
        }


@dataclasses.dataclass
class EvalSummary:
    """Aggregated outcome of one eval invocation."""

    eval_id: str            # ``v-<uuid12>``; "v" for "verified"
    batch_id: str           # the underlying batch's ID
    started_at: str
    finished_at: str
    duration_s: float
    output_dir: str
    total: int              # cases in the input
    passed: int             # scorer returned passed=True
    failed: int             # scorer returned passed=False (run completed)
    errored: int            # the underlying run didn't complete (error / timeout / etc.)
    pass_rate: float        # passed / total, 0.0 when total == 0
    avg_score: float        # mean(score) over completed scoring; 0.0 on empty
    by_scorer: dict[str, dict[str, int]] = dataclasses.field(default_factory=dict)
    # Populated only when --baseline DIR is supplied:
    baseline_id: str | None = None
    regressions: list[str] = dataclasses.field(default_factory=list)   # case_ids
    improvements: list[str] = dataclasses.field(default_factory=list)  # case_ids
    cases: list[EvalScore] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "eval_id": self.eval_id,
            "batch_id": self.batch_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(float(self.duration_s), 3),
            "output_dir": self.output_dir,
            "total": int(self.total),
            "passed": int(self.passed),
            "failed": int(self.failed),
            "errored": int(self.errored),
            "pass_rate": round(float(self.pass_rate), 4),
            "avg_score": round(float(self.avg_score), 4),
            "by_scorer": dict(self.by_scorer),
            "cases": [c.to_dict() for c in self.cases],
        }
        if self.baseline_id is not None:
            d["baseline_id"] = self.baseline_id
            d["regressions"] = list(self.regressions)
            d["improvements"] = list(self.improvements)
        return d

    def to_json(self, *, indent: int | None = 2) -> str:
        """``indent=2`` default — summaries are for human read
        + CI artifact storage. ``indent=None`` for parser-
        friendly one-liners (``--json`` mode of the CLI)."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def mint_eval_id() -> str:
    """``v-<uuid12>``. The "v" prefix is for "verified" /
    "evaluation" — distinct from ``b-`` batch IDs and ``r-``
    run IDs so an operator can tell IDs apart at a glance."""
    import uuid
    return f"v-{uuid.uuid4().hex[:12]}"


def excerpt(s: str | None, limit: int = _ACTUAL_EXCERPT_LIMIT) -> str:
    """Helper used at the summary boundary so case rows stay
    inspector-friendly. Strips newlines, caps length, appends …."""
    if s is None:
        return ""
    s = str(s).replace("\n", " ").replace("\r", " ").strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s
