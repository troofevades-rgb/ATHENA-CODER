"""The ``Agent`` sub-agent tool's result framing.

A fork returns its last assistant message regardless of HOW the turn
ended. A sub-agent that hit its step limit or a circuit breaker (e.g.
thrashing on a search that never returns anything) leaves a partial or
off-task last message; the Agent tool must FLAG that instead of
laundering it up to the parent as a finished answer — the dogfood
failure where a sub-agent thrashed for 22 minutes and returned a
confident, unrelated reply.
"""

from __future__ import annotations

import pytest

from athena.agent import fork as fork_mod
from athena.agent.core import _current_agent
from athena.tools.agent_tool import Agent as AgentTool


def _run_with_fork_result(monkeypatch: pytest.MonkeyPatch, result: fork_mod.ForkResult) -> str:
    """Call the Agent tool with fork() stubbed to return ``result`` and a
    non-None current agent installed (the tool refuses to run otherwise)."""

    def _fake_fork(parent: object, **kwargs: object) -> fork_mod.ForkResult:
        return result

    monkeypatch.setattr(fork_mod, "fork", _fake_fork)
    token = _current_agent.set(object())  # any non-None parent
    try:
        return AgentTool(prompt="do full OSINT research on X", subagent_type="general-purpose")
    finally:
        _current_agent.reset(token)


def test_clean_completion_returns_response_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(final_response="Here is the finished report.", stop_reason="completed"),
    )
    assert out == "Here is the finished report."
    assert "WARNING" not in out


def test_no_progress_stop_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(
            final_response="The answer is definitely Foo.",
            stop_reason="circuit_breaker:no_progress",
        ),
    )
    assert out.startswith("[WARNING:")
    assert "no progress" in out.lower()
    # The underlying (suspect) response is still passed through so the
    # parent can use it if it wants — just no longer as authoritative.
    assert "The answer is definitely Foo." in out


def test_step_limit_stop_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(final_response="partial...", stop_reason="step_limit"),
    )
    assert out.startswith("[WARNING:")
    assert "step limit" in out.lower()


@pytest.mark.parametrize(
    ("reason", "needle"),
    [
        ("circuit_breaker:identical_tool_calls", "repeating the same tool call"),
        ("circuit_breaker:provider_errors", "repeated provider errors"),
    ],
)
def test_each_circuit_breaker_label_is_rendered(
    monkeypatch: pytest.MonkeyPatch, reason: str, needle: str
) -> None:
    """Pin the distinctive text of every abnormal-stop label so a typo
    can't silently degrade it to the generic fallback."""
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(final_response="x", stop_reason=reason),
    )
    assert out.startswith("[WARNING:")
    assert needle in out


def test_unknown_or_missing_stop_reason_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A None stop reason (the turn never fired a clean stop, e.g. it was
    interrupted) is treated as not-clean — better to over-flag than to
    pass a truncated answer up as finished."""
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(final_response="...", stop_reason=None),
    )
    assert out.startswith("[WARNING:")
    assert "did not finish cleanly" in out


def test_fork_error_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_with_fork_result(
        monkeypatch,
        fork_mod.ForkResult(final_response="", error="boom", stop_reason="completed"),
    )
    assert out == "ERROR running sub-agent: boom"
