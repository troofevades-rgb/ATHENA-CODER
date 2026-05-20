"""Tests for the standalone sandboxed runner (athena.sandbox.runner).

The runner is the verify loop's command-execution surface — it's
a function-call parallel to the Bash tool's ``_spawn`` wrap.

Tests stub ``subprocess.run`` so no command actually runs; the
assertion is on the argv that gets built + the result decoded.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from athena.sandbox import runner


class _StubCompleted:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _cfg(**overrides) -> SimpleNamespace:
    base = {
        "sandbox_enabled": False,
        "sandbox_backend": "bwrap",
        "sandbox_allow_network": False,
        "sandbox_writable_paths": [],
        "sandbox_fallback": "warn",
        "bash_extra_denylist": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def captured_argv(monkeypatch):
    calls: dict = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _StubCompleted(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------------------
# Denylist floor
# ---------------------------------------------------------------------------


def test_denylist_blocks_before_run(monkeypatch):
    """Denylisted command → BlockedByPolicyError, subprocess never called."""
    called = {"hit": False}

    def fake_run(argv, **kwargs):
        called["hit"] = True
        return _StubCompleted()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(runner.BlockedByPolicyError):
        runner.run("rm -rf /", cfg=_cfg(sandbox_enabled=True))
    assert called["hit"] is False


# ---------------------------------------------------------------------------
# Sandbox-off: direct bash -c
# ---------------------------------------------------------------------------


def test_sandbox_disabled_runs_unwrapped(captured_argv):
    result = runner.run("echo hi", cfg=_cfg())
    assert result.exit_code == 0
    assert result.succeeded
    assert result.sandboxed is False
    argv = captured_argv["argv"]
    # Direct argv path: cmd /C on Windows, bash -c elsewhere.
    if argv[0] == "cmd":
        assert argv == ["cmd", "/C", "echo hi"]
    else:
        assert argv == ["/bin/bash", "-c", "echo hi"]


# ---------------------------------------------------------------------------
# Sandbox-on: bwrap wrapping
# ---------------------------------------------------------------------------


def test_sandboxed_command_is_bwrap_wrapped(captured_argv, monkeypatch):
    monkeypatch.setattr(runner, "is_bwrap_available", lambda: True)
    result = runner.run("pytest -q", cfg=_cfg(sandbox_enabled=True))
    assert result.sandboxed is True
    argv = captured_argv["argv"]
    assert argv[0] == "bwrap"
    sentinel = argv.index("--")
    inner = argv[sentinel + 1 :]
    assert inner[-2:] == ["-c", "pytest -q"]


# ---------------------------------------------------------------------------
# Sandbox-on, bwrap unavailable: fallback semantics
# ---------------------------------------------------------------------------


def test_fallback_warn_runs_unsandboxed(captured_argv, monkeypatch):
    monkeypatch.setattr(runner, "is_bwrap_available", lambda: False)
    result = runner.run(
        "echo hi",
        cfg=_cfg(sandbox_enabled=True, sandbox_fallback="warn"),
    )
    assert result.sandboxed is False
    assert result.exit_code == 0


def test_fallback_error_refuses(monkeypatch):
    monkeypatch.setattr(runner, "is_bwrap_available", lambda: False)
    with pytest.raises(runner.BlockedByPolicyError):
        runner.run(
            "echo hi",
            cfg=_cfg(sandbox_enabled=True, sandbox_fallback="error"),
        )


# ---------------------------------------------------------------------------
# Exit codes pass through
# ---------------------------------------------------------------------------


def test_non_zero_exit_propagates(monkeypatch):
    def fake_run(argv, **kwargs):
        return _StubCompleted(returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run("false", cfg=_cfg())
    assert result.exit_code == 2
    assert result.succeeded is False
    assert result.stderr == "boom"


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


def test_timeout_returns_124(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run("sleep 9999", cfg=_cfg(), timeout_s=1.0)
    assert result.exit_code == 124
    assert "timed out" in result.stderr


def test_spawn_failure_returns_127(monkeypatch):
    def fake_run(argv, **kwargs):
        raise OSError("bash not found")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.run("anything", cfg=_cfg())
    assert result.exit_code == 127
    assert "bash not found" in result.stderr
