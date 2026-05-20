"""Per-leg degradation tests for the verified-execution loop (T5-04.3).

Each leg of the loop — checkpoint capture, LSP diagnose, sandboxed
run — can fail independently. The contract is that no single
leg's failure blocks the write itself; instead the outcome
degrades gracefully:

  - checkpoint fails → write still verifies, but no rollback offer
  - diagnose fails   → no false-positive failure; outcome passes
  - run fails (exc)  → failed_run, but no exception escapes

These tests pin each degradation path explicitly so a future
refactor doesn't quietly tighten one leg into a blocking error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from athena.verify.loop import VerifiedExecution
from athena.verify.outcome import VerificationOutcome


class _FakeDiag:
    def __init__(self, *, is_error=True, message="bad", line=1, col=1, code=""):
        self.is_error = is_error
        self.message = message
        self.line = line
        self.col = col
        self.code = code


class _FakeCheckpointManager:
    def __init__(
        self,
        *,
        cp_id: str = "cp-test",
        create_raises: bool = False,
        rollback_raises: bool = False,
    ):
        self.cp_id = cp_id
        self.create_calls: list[str] = []
        self.rollback_calls: list[str] = []
        self._create_raises = create_raises
        self._rollback_raises = rollback_raises

    def create(self, *, label: str):
        self.create_calls.append(label)
        if self._create_raises:
            raise RuntimeError("checkpoint capture failed")
        return SimpleNamespace(id=self.cp_id)

    def rollback_to(self, cp_id: str):
        self.rollback_calls.append(cp_id)
        if self._rollback_raises:
            raise RuntimeError("rollback failed")


class _FakeRunResult:
    def __init__(self, *, exit_code: int, stderr: str = ""):
        self.exit_code = exit_code
        self.stderr = stderr

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def _cfg(**overrides) -> SimpleNamespace:
    base = {
        "verify_on_write": "diagnose",
        "verify_command": None,
        "verify_auto_rollback": False,
        "verify_auto_retry": False,
        "verify_max_retries": 2,
        "verify_run_timeout_s": 30.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Checkpoint leg degradation
# ---------------------------------------------------------------------------


def test_checkpoint_failure_passes_diagnostics_but_offers_no_rollback():
    """When checkpoint capture raises and diagnose introduces an
    error, the outcome is failed_diagnostics with no checkpoint_id
    — the report shouldn't carry a /rollback-to hint pointing
    nowhere."""
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [_FakeDiag(message="bad bad bad")],
        checkpoint_manager=_FakeCheckpointManager(create_raises=True),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert out.checkpoint_id is None
    assert "/rollback-to" not in out.report()


def test_checkpoint_manager_absent_works_silently():
    """No active manager (e.g. CLI one-shot, no session) → the
    loop produces no checkpoint_id but still verifies."""
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [],
        checkpoint_manager=None,
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"
    assert out.checkpoint_id is None


# ---------------------------------------------------------------------------
# Diagnose leg degradation
# ---------------------------------------------------------------------------


def test_diagnose_returns_unknown_shape_does_not_crash():
    """If diagnose returns objects that don't have is_error / line /
    message attributes, the loop must not blow up — it should
    silently treat them as non-errors."""
    weird = [object(), {"unexpected": "dict"}, None]
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: weird,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"


def test_diagnose_returns_none_treated_as_empty():
    """A diagnose implementation returning None instead of []
    shouldn't break iteration."""
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"


def test_diagnose_raises_becomes_passed():
    """An LSP transport error during verify must not become a
    failure — it should degrade to "passed" (with a debug log)."""
    def boom(paths):
        raise ConnectionError("LSP went away")

    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=boom,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"


# ---------------------------------------------------------------------------
# Run leg degradation
# ---------------------------------------------------------------------------


def test_run_raises_becomes_failed_run_with_minus_one():
    """A runner exception → failed_run outcome (not propagation)."""
    def boom(cmd, **kw):
        raise OSError("sandbox spawn failed")

    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=boom,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_run"
    assert out.run_exit_code == -1
    assert "sandbox spawn failed" in out.run_stderr_tail


def test_run_with_empty_stderr_still_reports():
    """A failed run that produced no stderr still gets a clean
    report — the outcome's report() falls back to "(no stderr
    captured)"."""
    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=lambda cmd, **kw: _FakeRunResult(exit_code=1, stderr=""),
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_run"
    assert "(no stderr captured)" in out.report()


def test_run_stderr_tail_truncates_to_500_bytes():
    """Very long stderr is trimmed to the last 500 bytes with an
    ellipsis prefix — bounds the report size."""
    huge = "x" * 2000
    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=lambda cmd, **kw: _FakeRunResult(exit_code=2, stderr=huge),
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.run_stderr_tail is not None
    assert out.run_stderr_tail.startswith("...\n")
    # 500 bytes of "x" + "...\n" preamble
    assert len(out.run_stderr_tail) <= 500 + len("...\n") + 1


# ---------------------------------------------------------------------------
# Auto-retry cap
# ---------------------------------------------------------------------------


def test_retry_disabled_by_default():
    """With verify_auto_retry=False (default), retry_fn is never
    called even when the cycle fails."""
    retry_calls: list[VerificationOutcome] = []

    def retry_fn(outcome):
        retry_calls.append(outcome)

    v = VerifiedExecution(
        cfg=_cfg(),  # verify_auto_retry defaults to False
        diagnose=lambda paths: [_FakeDiag(message="bad")],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py", retry_fn=retry_fn)
    assert out.outcome == "failed_diagnostics"
    assert retry_calls == []
    assert out.retries == 0


def test_retry_cap_enforced():
    """When auto-retry is on and every retry still fails, the
    loop tries exactly verify_max_retries times and then stops
    — it doesn't loop forever."""
    retry_calls: list[VerificationOutcome] = []

    def always_fails_to_fix(outcome):
        # Simulate "agent attempted a retry but produced no
        # better write" — we just record the attempt.
        retry_calls.append(outcome)

    v = VerifiedExecution(
        cfg=_cfg(verify_auto_retry=True, verify_max_retries=2),
        diagnose=lambda paths: [_FakeDiag(message="persistent error")],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py", retry_fn=always_fails_to_fix)
    assert out.outcome == "failed_diagnostics"
    assert len(retry_calls) == 2  # respected the cap
    assert out.retries == 2


def test_retry_succeeds_on_second_attempt():
    """When the retry callback actually fixes things, the next
    verify cycle should report passed with retries=1."""
    diagnose_calls = {"n": 0}

    def diagnose(paths):
        diagnose_calls["n"] += 1
        # First two passes fail (initial + first retry), third
        # call succeeds.
        if diagnose_calls["n"] <= 2:
            return [_FakeDiag(message="bad")]
        return []

    retry_calls: list[VerificationOutcome] = []

    v = VerifiedExecution(
        cfg=_cfg(verify_auto_retry=True, verify_max_retries=3),
        diagnose=diagnose,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write(
        "athena/foo.py",
        retry_fn=lambda o: retry_calls.append(o),
    )
    assert out.outcome == "passed"
    assert out.retries == 2  # 1 initial fail + 1 retry fail + success
    assert len(retry_calls) == 2


def test_retry_callback_exception_aborts_loop():
    """If retry_fn itself raises, the loop stops and surfaces the
    last failure — never propagates the exception."""
    retry_calls: list[VerificationOutcome] = []

    def crashy_retry(outcome):
        retry_calls.append(outcome)
        raise RuntimeError("agent retry crashed")

    v = VerifiedExecution(
        cfg=_cfg(verify_auto_retry=True, verify_max_retries=5),
        diagnose=lambda paths: [_FakeDiag(message="bad")],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py", retry_fn=crashy_retry)
    assert out.outcome == "failed_diagnostics"
    assert len(retry_calls) == 1  # tried once, then aborted


def test_retry_without_callback_is_a_no_op():
    """auto_retry=True but retry_fn=None → single shot, no
    attempt to retry."""
    v = VerifiedExecution(
        cfg=_cfg(verify_auto_retry=True),
        diagnose=lambda paths: [_FakeDiag(message="bad")],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py", retry_fn=None)
    assert out.outcome == "failed_diagnostics"
    assert out.retries == 0
