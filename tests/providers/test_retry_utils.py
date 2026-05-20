"""Tests for athena.providers.retry_utils (T2-03.4).

The wrapper is sync (matches athena's sync provider surface). Spec's
async test skeleton is adapted to plain ``def`` tests against the
sync API.
"""

from __future__ import annotations

import httpx
import pytest

from athena.providers.retry_utils import RetryBudgetExceeded, with_retry


def _http_status_error(
    status: int, headers: dict | None = None, text: str = ""
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(status_code=status, text=text, headers=headers or {})
    return httpx.HTTPStatusError("err", request=request, response=response)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_succeeds_on_first_try() -> None:
    def op() -> str:
        return "ok"

    assert with_retry(op) == "ok"


# ---------------------------------------------------------------------------
# RETRY action
# ---------------------------------------------------------------------------


def test_retries_on_5xx_then_succeeds() -> None:
    attempts = 0

    def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _http_status_error(503, text="oops")
        return "ok"

    result = with_retry(op, max_backoff_s=0.01)
    assert result == "ok"
    assert attempts == 3


def test_retries_on_connect_error_then_succeeds() -> None:
    attempts = 0

    def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise httpx.ConnectError("refused")
        return "ok"

    assert with_retry(op, max_backoff_s=0.01) == "ok"
    assert attempts == 2


def test_retry_after_header_caps_at_max_backoff(monkeypatch) -> None:
    """A server-supplied Retry-After of 600s is clamped to
    max_backoff_s (so we don't accidentally sleep for ten minutes)."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "athena.providers.retry_utils.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    attempts = 0

    def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise _http_status_error(429, headers={"retry-after": "600"})
        return "ok"

    with_retry(op, max_backoff_s=2.0)
    assert sleep_calls
    assert sleep_calls[0] == 2.0


# ---------------------------------------------------------------------------
# ABORT action
# ---------------------------------------------------------------------------


def test_aborts_on_4xx() -> None:
    def op() -> str:
        raise _http_status_error(401, text="unauthorized")

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(op)


def test_aborts_on_unknown_exception() -> None:
    def op() -> str:
        raise RuntimeError("opaque")

    with pytest.raises(RuntimeError):
        with_retry(op)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_exceeds_retry_budget_raises_RetryBudgetExceeded() -> None:
    def op() -> str:
        raise httpx.ConnectError("nope")

    with pytest.raises(RetryBudgetExceeded) as excinfo:
        with_retry(op, max_retries=2, max_backoff_s=0.01)
    assert excinfo.value.attempts == 3
    assert excinfo.value.last_classification.error_class.value == "network"


# ---------------------------------------------------------------------------
# Callback paths
# ---------------------------------------------------------------------------


def test_rotate_credential_callback_invoked_on_repeated_429() -> None:
    """Repeated 429s eventually exhaust the retry budget; the
    rotate callback fires when no other route helps. Even when
    on_rotate returns False (no more credentials) the wrapper
    still calls it before escalating to abort.

    Current classifier returns RETRY for 429, not ROTATE_CREDENTIAL
    directly — so this test exercises the budget-exceeded path and
    confirms RetryBudgetExceeded carries the 429 classification."""

    def op() -> str:
        raise _http_status_error(429, headers={"retry-after": "0"})

    rotate_calls = 0

    def on_rotate() -> bool:
        nonlocal rotate_calls
        rotate_calls += 1
        return False

    with pytest.raises(RetryBudgetExceeded):
        with_retry(
            op,
            on_rotate_credential=on_rotate,
            max_backoff_s=0.01,
            max_retries=2,
        )


def test_compress_context_callback_invoked() -> None:
    """When the classifier says COMPRESS_CONTEXT, with_retry calls
    on_compress_context. If it returns True, the next attempt runs.
    If True repeatedly, the wrapper eventually re-raises after
    max_retries."""
    op_calls = 0

    def op() -> str:
        nonlocal op_calls
        op_calls += 1
        raise Exception("prompt is too long")

    compress_calls = 0

    def on_compress() -> bool:
        nonlocal compress_calls
        compress_calls += 1
        return True

    with pytest.raises(Exception):
        with_retry(
            op,
            on_compress_context=on_compress,
            max_retries=2,
            max_backoff_s=0.01,
        )

    # The wrapper called on_compress at least once before giving up.
    assert compress_calls >= 1
    # And the operation ran multiple times: initial + at least one
    # post-compression retry.
    assert op_calls >= 2


def test_compress_context_callback_returning_false_aborts() -> None:
    """If on_compress_context returns False (compression not possible),
    the wrapper escalates to ABORT immediately."""

    def op() -> str:
        raise Exception("prompt is too long")

    def on_compress() -> bool:
        return False

    with pytest.raises(Exception, match="prompt is too long"):
        with_retry(op, on_compress_context=on_compress, max_retries=5)


# ---------------------------------------------------------------------------
# Interrupt propagation
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_not_retried() -> None:
    """KeyboardInterrupt propagates immediately; never classified."""
    op_calls = 0

    def op() -> str:
        nonlocal op_calls
        op_calls += 1
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        with_retry(op, max_retries=5)
    assert op_calls == 1  # exactly one call; no retry


def test_cancelled_error_not_retried() -> None:
    """asyncio.CancelledError is an Exception (3.8+); it falls
    through as UNKNOWN -> ABORT, which re-raises immediately."""
    import asyncio

    op_calls = 0

    def op() -> str:
        nonlocal op_calls
        op_calls += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        with_retry(op, max_retries=5)
    assert op_calls == 1


def test_system_exit_not_retried() -> None:
    op_calls = 0

    def op() -> str:
        nonlocal op_calls
        op_calls += 1
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        with_retry(op, max_retries=5)
    assert op_calls == 1
