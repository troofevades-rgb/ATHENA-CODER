"""Tests for the verified-execution orchestrator (T5-04.2).

The loop is the integration point of three Tier-5 pieces — T3-03
checkpoints, T5-03 LSP diagnostics, and T5-02 sandboxed run. The
tests use injected doubles for each leg so the loop logic is
exercised in isolation, with no real LSP server or bwrap host
required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from athena.verify.loop import VerifiedExecution
from athena.verify.outcome import VerificationOutcome

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


class _FakeDiag:
    """Diagnostic-shape stub mirroring the bits the loop touches."""

    def __init__(
        self, *, is_error: bool, message: str, line: int = 1, col: int = 1, code: str = ""
    ):
        self.is_error = is_error
        self.message = message
        self.line = line
        self.col = col
        self.code = code


class _FakeCheckpoint:
    def __init__(self, id_: str = "cp-test123"):
        self.id = id_


class _FakeCheckpointManager:
    def __init__(self, *, cp_id: str = "cp-test123", create_raises: bool = False):
        self.cp_id = cp_id
        self.create_calls: list[str] = []
        self.rollback_calls: list[str] = []
        self._create_raises = create_raises

    def create(self, *, label: str):
        self.create_calls.append(label)
        if self._create_raises:
            raise RuntimeError("simulated checkpoint failure")
        return _FakeCheckpoint(self.cp_id)

    def rollback_to(self, cp_id: str):
        self.rollback_calls.append(cp_id)


class _FakeRunResult:
    def __init__(self, *, exit_code: int, stderr: str = "", stdout: str = ""):
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def _cfg(**overrides) -> SimpleNamespace:
    """Tiny cfg double matching the attribute surface the loop reads."""
    defaults = {
        "verify_on_write": "diagnose",
        "verify_command": None,
        "verify_auto_rollback": False,
        "verify_auto_retry": False,
        "verify_max_retries": 2,
        "verify_run_timeout_s": 30.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_passed_when_diagnose_clean():
    cps = _FakeCheckpointManager(cp_id="cp-aaa")
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [],
        checkpoint_manager=cps,
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"
    assert out.checkpoint_id == "cp-aaa"
    assert cps.create_calls == ["pre-write-foo.py"]


def test_skipped_when_off():
    v = VerifiedExecution(cfg=_cfg(verify_on_write="off"))
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "skipped"


# ---------------------------------------------------------------------------
# Diagnose leg
# ---------------------------------------------------------------------------


def test_failed_diagnostics_extracts_errors():
    diags = [
        _FakeDiag(is_error=True, message="undefined name 'x'", line=10, col=4),
        _FakeDiag(is_error=False, message="line too long"),  # warning — ignored
    ]
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: diags,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert len(out.introduced_errors) == 1
    assert "undefined name 'x'" in out.introduced_errors[0]
    assert out.checkpoint_id == "cp-test123"
    assert not out.rolled_back


def test_diagnose_exception_becomes_passed():
    """An LSP failure during verify must not block the write."""

    def boom(paths):
        raise RuntimeError("LSP exploded")

    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=boom,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"


# ---------------------------------------------------------------------------
# Run leg
# ---------------------------------------------------------------------------


def test_run_failure_yields_failed_run():
    v = VerifiedExecution(
        cfg=_cfg(
            verify_on_write="diagnose+run",
            verify_command="pytest -q",
        ),
        diagnose=lambda paths: [],
        runner=lambda cmd, **kw: _FakeRunResult(
            exit_code=1,
            stderr="E   AssertionError: 1 != 2\n",
        ),
        checkpoint_manager=_FakeCheckpointManager(cp_id="cp-zzz"),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_run"
    assert out.run_exit_code == 1
    assert "AssertionError" in out.run_stderr_tail
    assert out.checkpoint_id == "cp-zzz"
    # report mentions the rollback hint
    assert "/rollback-to cp-zzz" in out.report()


def test_run_success_falls_through_to_passed():
    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=lambda cmd, **kw: _FakeRunResult(exit_code=0),
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"


def test_run_not_invoked_when_no_command():
    """diagnose+run mode with no verify_command falls through to passed."""
    runner_calls = []

    def runner(cmd, **kw):
        runner_calls.append(cmd)
        return _FakeRunResult(exit_code=0)

    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command=None),
        diagnose=lambda paths: [],
        runner=runner,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"
    assert runner_calls == []


def test_run_not_invoked_in_diagnose_only_mode():
    runner_calls = []

    def runner(cmd, **kw):
        runner_calls.append(cmd)
        return _FakeRunResult(exit_code=0)

    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=runner,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"
    assert runner_calls == []


def test_run_exception_yields_failed_run():
    def boom(cmd, **kw):
        raise RuntimeError("subprocess explosion")

    v = VerifiedExecution(
        cfg=_cfg(verify_on_write="diagnose+run", verify_command="pytest -q"),
        diagnose=lambda paths: [],
        runner=boom,
        checkpoint_manager=_FakeCheckpointManager(),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_run"
    assert out.run_exit_code == -1
    assert "subprocess explosion" in out.run_stderr_tail


# ---------------------------------------------------------------------------
# Checkpoint absence
# ---------------------------------------------------------------------------


def test_no_checkpoint_manager_means_no_rollback_offer():
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [_FakeDiag(is_error=True, message="bad")],
        checkpoint_manager=None,  # explicit
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert out.checkpoint_id is None
    assert "/rollback-to" not in out.report()


def test_checkpoint_capture_failure_does_not_block_write():
    """create() raising → outcome still passes if diagnose passes,
    but checkpoint_id is None."""
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [],
        checkpoint_manager=_FakeCheckpointManager(create_raises=True),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "passed"
    assert out.checkpoint_id is None


# ---------------------------------------------------------------------------
# Auto-rollback
# ---------------------------------------------------------------------------


def test_auto_rollback_reverts_and_marks_outcome():
    cps = _FakeCheckpointManager(cp_id="cp-xyz")
    v = VerifiedExecution(
        cfg=_cfg(verify_auto_rollback=True),
        diagnose=lambda paths: [_FakeDiag(is_error=True, message="boom")],
        checkpoint_manager=cps,
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert out.rolled_back is True
    assert cps.rollback_calls == ["cp-xyz"]
    # Report swaps the offer for the "auto-rolled back" marker
    assert "auto-rolled back" in out.report()
    assert "/rollback-to" not in out.report()


def test_auto_rollback_without_checkpoint_is_a_no_op():
    """When checkpoint capture failed but the write itself still
    introduced errors, auto-rollback can't revert. The outcome
    surfaces the failure without a rollback marker."""
    v = VerifiedExecution(
        cfg=_cfg(verify_auto_rollback=True),
        diagnose=lambda paths: [_FakeDiag(is_error=True, message="boom")],
        checkpoint_manager=_FakeCheckpointManager(create_raises=True),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert out.rolled_back is False
    assert out.checkpoint_id is None


def test_auto_rollback_exception_logs_and_returns_unmarked():
    """A rollback that raises must not blow up the verify call."""

    class _MgrRollbackBoom(_FakeCheckpointManager):
        def rollback_to(self, cp_id: str):
            raise RuntimeError("rollback failed")

    v = VerifiedExecution(
        cfg=_cfg(verify_auto_rollback=True),
        diagnose=lambda paths: [_FakeDiag(is_error=True, message="boom")],
        checkpoint_manager=_MgrRollbackBoom(cp_id="cp-xyz"),
    )
    out = v.verify_write("athena/foo.py")
    assert out.outcome == "failed_diagnostics"
    assert out.rolled_back is False  # rollback failed mid-flight


# ---------------------------------------------------------------------------
# latest_outcome accessor (T5-07 prep)
# ---------------------------------------------------------------------------


def test_latest_outcome_tracks_most_recent_call():
    v = VerifiedExecution(
        cfg=_cfg(),
        diagnose=lambda paths: [],
        checkpoint_manager=_FakeCheckpointManager(),
    )
    assert v.latest_outcome is None
    a = v.verify_write("a.py")
    assert v.latest_outcome is a
    b = v.verify_write("b.py")
    assert v.latest_outcome is b
    assert isinstance(v.latest_outcome, VerificationOutcome)
