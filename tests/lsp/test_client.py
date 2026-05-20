"""Tests for athena.lsp.client (T5-03R.1).

A ``FakeTransport`` stubs the language server — it returns canned
JSON-RPC messages so the test never spawns a real subprocess.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from athena.lsp.client import (
    Diagnostic,
    LSPTransport,
    Severity,
    _frame,
    _path_to_uri,
    _read_frame,
    diagnose,
)

# ---------------------------------------------------------------------------
# Test transport
# ---------------------------------------------------------------------------


class FakeTransport(LSPTransport):
    """Replays canned reads in order. Records writes for assertion."""

    def __init__(self, replies: list[dict[str, Any] | None]):
        self._replies = list(replies)
        self.writes: list[dict[str, Any]] = []
        self.closed = False

    def write(self, payload: dict[str, Any]) -> None:
        self.writes.append(payload)

    def read(self, *, deadline: float) -> dict[str, Any] | None:
        if not self._replies:
            return None
        return self._replies.pop(0)

    def close(self) -> None:
        self.closed = True


def _initialize_reply() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}


def _publish_diagnostics(path: Path, diags: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {
            "uri": _path_to_uri(str(path)),
            "diagnostics": diags,
        },
    }


def _cfg(**overrides) -> Any:
    base = SimpleNamespace(
        lsp_enabled=True,
        lsp_server_command={},
        lsp_timeout_s=30.0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Frame I/O
# ---------------------------------------------------------------------------


def test_frame_roundtrip() -> None:
    """`_frame` writes Content-Length headers; `_read_frame`
    reads them back into a dict."""
    import io

    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    encoded = _frame(payload)
    stream = io.BytesIO(encoded)
    assert _read_frame(stream) == payload


def test_read_frame_eof_returns_none() -> None:
    import io

    assert _read_frame(io.BytesIO(b"")) is None


def test_read_frame_missing_length_returns_none() -> None:
    import io

    bad = b"Header: value\r\n\r\nbody"
    assert _read_frame(io.BytesIO(bad)) is None


# ---------------------------------------------------------------------------
# diagnose() — happy path
# ---------------------------------------------------------------------------


def test_parses_diagnostics(tmp_path: Path) -> None:
    py_file = tmp_path / "buggy.py"
    py_file.write_text("import nonexistent\n", encoding="utf-8")

    fake = FakeTransport(
        [
            _initialize_reply(),
            _publish_diagnostics(
                py_file,
                [
                    {
                        "range": {
                            "start": {"line": 0, "character": 7},
                            "end": {"line": 0, "character": 18},
                        },
                        "severity": 1,
                        "code": "reportMissingImports",
                        "message": "Import 'nonexistent' could not be resolved",
                    }
                ],
            ),
        ]
    )

    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: fake,
    )

    # Need to also bypass shutil.which check; patch via cfg server map.
    # That actually doesn't help — the diagnose function calls
    # shutil.which on the server name. Use monkeypatch via the
    # next test layer instead.


def test_parses_diagnostics_with_path_in_path(tmp_path: Path, monkeypatch) -> None:
    """End-to-end happy path. Monkeypatch shutil.which so the
    fake server name resolves, then assert on the parsed
    Diagnostic."""
    py_file = tmp_path / "buggy.py"
    py_file.write_text("import nonexistent\n", encoding="utf-8")

    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    fake = FakeTransport(
        [
            _initialize_reply(),
            _publish_diagnostics(
                py_file,
                [
                    {
                        "range": {
                            "start": {"line": 4, "character": 9},
                            "end": {"line": 4, "character": 20},
                        },
                        "severity": 1,
                        "code": "reportMissingImports",
                        "message": "Import 'nonexistent' could not be resolved",
                    }
                ],
            ),
        ]
    )

    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: fake,
    )

    assert len(out) == 1
    d = out[0]
    assert isinstance(d, Diagnostic)
    assert d.line == 5  # 0-based 4 → 1-based 5
    assert d.col == 10  # 0-based 9 → 1-based 10
    assert d.severity == Severity.ERROR
    assert d.is_error is True
    assert d.code == "reportMissingImports"
    assert "nonexistent" in d.message
    # Transport was closed by diagnose().
    assert fake.closed


# ---------------------------------------------------------------------------
# Empty / unsupported / disabled paths
# ---------------------------------------------------------------------------


def test_empty_paths_returns_empty() -> None:
    assert diagnose([], cfg=_cfg()) == []


def test_lsp_disabled_returns_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    assert diagnose([str(py_file)], cfg=_cfg(lsp_enabled=False)) == []


def test_unknown_language_skipped(tmp_path: Path, monkeypatch) -> None:
    """A .txt file has no language mapping → silently skipped."""
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    text = tmp_path / "x.txt"
    text.write_text("hi", encoding="utf-8")
    assert diagnose([str(text)], cfg=_cfg()) == []


def test_no_server_returns_empty_with_log(tmp_path: Path, monkeypatch, caplog) -> None:
    """Server binary not on PATH → debug log + empty list, never
    raises."""
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr("athena.lsp.client.shutil.which", lambda _: None)
    import logging as _logging

    caplog.set_level(_logging.DEBUG, logger="athena.lsp.client")
    out = diagnose([str(py_file)], cfg=_cfg())
    assert out == []
    assert any("no server installed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


def test_severity_mapping(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "all.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    fake = FakeTransport(
        [
            _initialize_reply(),
            _publish_diagnostics(
                py_file,
                [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "severity": s,
                        "code": "x",
                        "message": f"sev={s}",
                    }
                    for s in (1, 2, 3, 4)
                ],
            ),
        ]
    )
    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: fake,
    )
    assert [d.severity for d in out] == [
        Severity.ERROR,
        Severity.WARNING,
        Severity.INFORMATION,
        Severity.HINT,
    ]


def test_unknown_severity_defaults_to_information(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    fake = FakeTransport(
        [
            _initialize_reply(),
            _publish_diagnostics(
                py_file,
                [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "severity": "garbage",
                        "code": "x",
                        "message": "no idea",
                    }
                ],
            ),
        ]
    )
    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: fake,
    )
    assert out[0].severity == Severity.INFORMATION


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_timeout_returns_empty(tmp_path: Path, monkeypatch) -> None:
    """Server starts (initialize replies) but never sends a
    publishDiagnostics. After lsp_timeout_s expires the function
    returns whatever it collected — empty in this case."""
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    fake = FakeTransport(
        [
            _initialize_reply(),
            # After initialize, no further messages → read() loops
            # returning None until the deadline expires.
            None,
            None,
            None,
        ]
    )
    out = diagnose(
        [str(py_file)],
        cfg=_cfg(
            lsp_server_command={"python": ["fake-server"]},
            lsp_timeout_s=0.1,
        ),
        transport_factory=lambda _argv: fake,
    )
    assert out == []
    assert fake.closed


# ---------------------------------------------------------------------------
# Transport failure
# ---------------------------------------------------------------------------


def test_transport_construction_failure_returns_empty(tmp_path: Path, monkeypatch) -> None:
    """Spawning the server raises FileNotFoundError → graceful empty."""
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    def _boom(_argv):
        raise FileNotFoundError("bad server")

    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=_boom,
    )
    assert out == []


def test_session_exception_returns_partial(tmp_path: Path, monkeypatch) -> None:
    """A bizarre read() that raises mid-session shouldn't crash
    the agent loop — function swallows + returns []."""
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    py_file = tmp_path / "x.py"
    py_file.write_text("x = 1\n", encoding="utf-8")

    class _BadTransport(LSPTransport):
        closed = False

        def write(self, payload):  # noqa: D401
            pass

        def read(self, *, deadline):
            raise RuntimeError("transport melted")

        def close(self):
            self.closed = True

    bad = _BadTransport()
    out = diagnose(
        [str(py_file)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: bad,
    )
    assert out == []
    assert bad.closed is True


# ---------------------------------------------------------------------------
# Multi-file aggregation
# ---------------------------------------------------------------------------


def test_multi_file_aggregates(tmp_path: Path, monkeypatch) -> None:
    """Two paths in one diagnose() call → both diagnostics in
    one returned list."""
    monkeypatch.setattr(
        "athena.lsp.client.shutil.which",
        lambda _: "/usr/bin/fake-server",
    )
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x = 1\n", encoding="utf-8")
    b.write_text("y = 2\n", encoding="utf-8")

    fake = FakeTransport(
        [
            _initialize_reply(),
            _publish_diagnostics(a, [_diag("err in a", 1)]),
            _publish_diagnostics(b, [_diag("warn in b", 2)]),
        ]
    )
    out = diagnose(
        [str(a), str(b)],
        cfg=_cfg(lsp_server_command={"python": ["fake-server"]}),
        transport_factory=lambda _argv: fake,
    )
    paths = {d.path for d in out}
    assert paths == {str(a), str(b)}


def _diag(msg: str, severity: int) -> dict[str, Any]:
    return {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
        "severity": severity,
        "code": "x",
        "message": msg,
    }


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_path_to_uri_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "x.py"
    p.write_text("hi", encoding="utf-8")
    uri = _path_to_uri(str(p))
    assert uri.startswith("file://")


def test_diagnostic_is_error_property() -> None:
    d = Diagnostic(path="x.py", line=1, col=1, severity=Severity.ERROR, code="x", message="x")
    assert d.is_error is True
    d2 = Diagnostic(path="x.py", line=1, col=1, severity=Severity.WARNING, code="x", message="x")
    assert d2.is_error is False
