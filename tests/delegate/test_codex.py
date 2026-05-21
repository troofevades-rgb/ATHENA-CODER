"""Tests for the Codex-CLI helper module + the delegate CLI.

Stubs subprocess so the suite doesn't depend on codex being
installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from athena.delegate.codex import (
    CodexDetection,
    RECOMMENDED_COMMAND,
    detect_codex,
    recommended_config_snippet,
    write_config_snippet,
)


# ---------------------------------------------------------------
# detect_codex
# ---------------------------------------------------------------


def test_detect_codex_not_found(monkeypatch):
    """which() returns None → CodexDetection(found=False, error...)."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    d = detect_codex()
    assert d.found is False
    assert d.path is None
    assert d.version is None
    assert d.error is not None
    assert "not found on PATH" in d.error
    # Install hints in the error message.
    assert "npm install" in d.error
    assert "brew install" in d.error


def test_detect_codex_found_with_version(monkeypatch, tmp_path: Path):
    """which() returns a path; subprocess returns a version."""
    fake = tmp_path / "codex"
    fake.touch()
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))

    def _fake_run(argv, *_a, **_kw):
        return SimpleNamespace(
            returncode=0,
            stdout="codex 0.42.1\n", stderr="",
        )
    monkeypatch.setattr(subprocess, "run", _fake_run)

    d = detect_codex()
    assert d.found is True
    assert d.path == str(fake)
    assert d.version == "codex 0.42.1"
    assert d.error is None


def test_detect_codex_found_but_version_fails(monkeypatch, tmp_path: Path):
    """Binary exists but --version times out / errors. Detection
    still reports found=True (the binary IS there) with version=None."""
    fake = tmp_path / "codex"
    fake.touch()
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))

    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=10)
    monkeypatch.setattr(subprocess, "run", _timeout)

    d = detect_codex()
    assert d.found is True
    assert d.version is None
    assert d.error is None


def test_detect_codex_custom_binary_name(monkeypatch):
    """An operator with a wrapper script can pass a different
    binary name."""
    seen: list[str] = []
    def _which(name):
        seen.append(name)
        return None
    monkeypatch.setattr(shutil, "which", _which)
    detect_codex(binary_name="my-codex")
    assert seen == ["my-codex"]


# ---------------------------------------------------------------
# recommended_config_snippet
# ---------------------------------------------------------------


def test_snippet_contains_required_fields():
    s = recommended_config_snippet()
    assert "cli_delegate_enabled = true" in s
    assert RECOMMENDED_COMMAND in s
    assert "cli_delegate_sandbox = true" in s
    assert "cli_delegate_timeout_s" in s


def test_snippet_sandbox_off_when_requested():
    s = recommended_config_snippet(sandbox=False)
    assert "cli_delegate_sandbox = false" in s


def test_recommended_command_uses_exec_quiet():
    """The canonical Codex non-interactive form. {task} is the
    delegate_to_cli placeholder."""
    assert RECOMMENDED_COMMAND == "codex exec --quiet {task}"


# ---------------------------------------------------------------
# write_config_snippet
# ---------------------------------------------------------------


def test_write_config_snippet_creates_file(tmp_path: Path):
    target = tmp_path / "config.toml"
    written = write_config_snippet(config_path=target)
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "cli_delegate_command" in text
    assert RECOMMENDED_COMMAND in text


def test_write_config_snippet_appends_to_existing(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text('model = "test-model"\n', encoding="utf-8")
    write_config_snippet(config_path=target)
    text = target.read_text(encoding="utf-8")
    # Existing key preserved.
    assert 'model = "test-model"' in text
    # Snippet appended.
    assert "cli_delegate_command" in text


def test_write_config_snippet_refuses_when_already_configured(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text(
        'cli_delegate_command = "aider --message {task}"\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="already configures"):
        write_config_snippet(config_path=target)


def test_write_config_snippet_overwrite_appends_anyway(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text(
        'cli_delegate_command = "aider --message {task}"\n',
        encoding="utf-8",
    )
    write_config_snippet(config_path=target, overwrite=True)
    text = target.read_text(encoding="utf-8")
    # Both lines present — TOML last-value-wins decides at load
    # time. The docstring warns about this; the operator
    # cleans up.
    assert text.count("cli_delegate_command") == 2


def test_write_config_snippet_creates_parent_dir(tmp_path: Path):
    deep = tmp_path / "deep" / "nested" / "config.toml"
    write_config_snippet(config_path=deep)
    assert deep.exists()


# ---------------------------------------------------------------
# CLI integration: athena delegate verify + setup-codex
# ---------------------------------------------------------------


def test_cli_verify_when_not_configured(monkeypatch, capsys):
    from athena.cli.delegate import main as delegate_main

    cfg = SimpleNamespace(
        cli_delegate_enabled=False,
        cli_delegate_command=None,
        cli_delegate_sandbox=True,
    )
    monkeypatch.setattr("athena.cli.delegate.load_config", lambda: cfg)

    code = delegate_main(["verify"])
    out = capsys.readouterr().out
    assert code == 1
    assert "not configured" in out


def test_cli_verify_with_missing_binary(monkeypatch, capsys):
    from athena.cli.delegate import main as delegate_main

    cfg = SimpleNamespace(
        cli_delegate_enabled=True,
        cli_delegate_command="nonexistent-cli {task}",
        cli_delegate_sandbox=True,
    )
    monkeypatch.setattr("athena.cli.delegate.load_config", lambda: cfg)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    code = delegate_main(["verify"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out
    assert "not found on PATH" in out


def test_cli_verify_happy_path(monkeypatch, capsys, tmp_path: Path):
    from athena.cli.delegate import main as delegate_main

    fake = tmp_path / "codex"
    fake.touch()
    cfg = SimpleNamespace(
        cli_delegate_enabled=True,
        cli_delegate_command=RECOMMENDED_COMMAND,
        cli_delegate_sandbox=True,
    )
    monkeypatch.setattr("athena.cli.delegate.load_config", lambda: cfg)
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="codex 0.42.1\n", stderr="",
        ),
    )

    code = delegate_main(["verify"])
    out = capsys.readouterr().out
    assert code == 0
    assert "OK" in out
    assert "codex 0.42.1" in out


def test_cli_verify_json_mode(monkeypatch, capsys, tmp_path: Path):
    from athena.cli.delegate import main as delegate_main

    fake = tmp_path / "codex"
    fake.touch()
    cfg = SimpleNamespace(
        cli_delegate_enabled=True,
        cli_delegate_command=RECOMMENDED_COMMAND,
        cli_delegate_sandbox=True,
    )
    monkeypatch.setattr("athena.cli.delegate.load_config", lambda: cfg)
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="codex 0.42.1\n", stderr="",
        ),
    )

    code = delegate_main(["verify", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload["ok"] is True
    assert payload["enabled"] is True
    assert payload["sandbox"] is True
    assert payload["binary"] == "codex"


def test_cli_setup_codex_when_not_installed(monkeypatch, capsys):
    from athena.cli.delegate import main as delegate_main
    monkeypatch.setattr(shutil, "which", lambda _: None)

    code = delegate_main(["setup-codex"])
    out = capsys.readouterr().out
    assert code == 1
    assert "NOT found" in out
    assert "npm install" in out


def test_cli_setup_codex_dry_run(monkeypatch, capsys, tmp_path: Path):
    from athena.cli.delegate import main as delegate_main

    fake = tmp_path / "codex"
    fake.touch()
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="codex 0.42.1\n", stderr="",
        ),
    )

    code = delegate_main(["setup-codex", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    # Dry-run shows what WOULD be written, doesn't write.
    assert "dry-run" in out.lower()
    assert RECOMMENDED_COMMAND in out


def test_cli_setup_codex_writes_with_yes(
    monkeypatch, capsys, tmp_path: Path,
):
    from athena.cli.delegate import main as delegate_main

    fake = tmp_path / "codex"
    fake.touch()
    config = tmp_path / "config.toml"
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="codex 0.42.1\n", stderr="",
        ),
    )

    code = delegate_main([
        "setup-codex",
        "--config-path", str(config),
        "--yes",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert config.exists()
    assert RECOMMENDED_COMMAND in config.read_text(encoding="utf-8")


def test_cli_setup_codex_detect_only(monkeypatch, capsys, tmp_path: Path):
    from athena.cli.delegate import main as delegate_main

    fake = tmp_path / "codex"
    fake.touch()
    monkeypatch.setattr(shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="codex 0.42.1\n", stderr="",
        ),
    )

    code = delegate_main(["setup-codex", "--detect-only"])
    out = capsys.readouterr().out
    assert code == 0
    assert "codex found" in out
    assert "0.42.1" in out
