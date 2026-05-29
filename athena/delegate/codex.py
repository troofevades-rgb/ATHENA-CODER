"""Codex-CLI-specific helpers for delegate_to_cli.

The generic ``delegate_to_cli`` tool (T6-03) works with any
external coding CLI via ``cfg.cli_delegate_command`` — the
canonical Codex form is ``"codex exec --quiet {task}"``.

This module adds the codex-specific bits operators need:
binary detection (where does codex live on this host?),
a recommended command template, and a config snippet writer
so first-time setup is one command, not three. Vendor-isolated
to this one file — a future ``aider.py`` / ``cursor_cli.py``
sibling would follow the same shape.

Doesn't replace ``delegate_to_cli``; just makes wiring it up
to Codex friction-free.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


# The canonical non-interactive Codex invocation. ``--quiet``
# suppresses Codex's own progress chatter so athena's delegate
# layer just sees the final git diff. ``{task}`` is the
# placeholder athena's adapter substitutes via shlex.
RECOMMENDED_COMMAND = "codex exec --quiet {task}"


class CodexDetection(NamedTuple):
    """Result of looking for the codex CLI on this host."""

    found: bool
    path: str | None  # absolute path to the binary, when found
    version: str | None  # version string from ``codex --version``, when found
    error: str | None  # explanation when not found / not runnable


def detect_codex(*, binary_name: str = "codex") -> CodexDetection:
    """Look up the codex binary + capture its version.

    Returns a ``CodexDetection`` (not raise) so callers can
    branch cleanly on absence — first-time setup wants to
    tell the operator "install codex first", not crash.
    """
    location = shutil.which(binary_name)
    if location is None:
        return CodexDetection(
            found=False,
            path=None,
            version=None,
            error=(
                f"{binary_name!r} not found on PATH. Install with:\n"
                "  npm install -g @openai/codex   # Node\n"
                "  brew install openai-codex      # macOS\n"
                "  (or follow https://github.com/openai/codex for "
                "the Rust / pre-built binary path on your platform)"
            ),
        )

    # Capture the version so the operator sees what they have
    # installed when verifying the wire-up. We use --version
    # with a short timeout; some codex builds spell it
    # differently — fall back to empty string rather than
    # erroring out.
    version = None
    try:
        out = subprocess.run(
            [location, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            version = out.stdout.strip().splitlines()[0]
    except (subprocess.TimeoutExpired, OSError) as e:
        # The binary exists but won't respond — surface
        # something rather than the bare path.
        logger.debug("codex --version failed: %s", e)
        version = None

    return CodexDetection(
        found=True,
        path=location,
        version=version,
        error=None,
    )


def recommended_config_snippet(*, sandbox: bool = True) -> str:
    """Return the canonical config block an operator can paste
    into ``~/.athena/config.toml`` to wire athena to codex.

    ``sandbox=True`` (default) routes the codex invocation
    through athena's T5-02 bubblewrap sandbox on Linux — the
    safer default, with predictable filesystem isolation.
    """
    return (
        "# Codex CLI delegation (T6-03 + codex helper)\n"
        "cli_delegate_enabled = true\n"
        f'cli_delegate_command = "{RECOMMENDED_COMMAND}"\n'
        f"cli_delegate_sandbox = {str(sandbox).lower()}\n"
        "cli_delegate_timeout_s = 600.0\n"
    )


def write_config_snippet(
    *,
    config_path: Path | str | None = None,
    sandbox: bool = True,
    overwrite: bool = False,
) -> Path:
    """Append the recommended codex config to ``config_path``
    (default ``~/.athena/config.toml``). Returns the path
    written.

    Refuses to write when:
      - ``cli_delegate_command`` already appears in the file
        and ``overwrite=False`` (the operator shouldn't have
        their existing config silently replaced)
      - the file is unreadable

    With ``overwrite=True``, the existing ``cli_delegate_*``
    lines are NOT removed — the snippet is appended; TOML's
    last-value-wins semantics mean the new values take effect.
    A clean rewrite is the operator's job (with a real diff
    review).
    """
    target = (
        Path(config_path).expanduser() if config_path else Path.home() / ".athena" / "config.toml"
    )
    snippet = recommended_config_snippet(sandbox=sandbox)

    existing = ""
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"cannot read existing config at {target}: {e}") from e
        if "cli_delegate_command" in existing and not overwrite:
            raise RuntimeError(
                f"{target} already configures cli_delegate_command; "
                "pass overwrite=True to append anyway (existing keys "
                "are NOT removed)"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        (existing + "\n" + snippet if existing else snippet),
        encoding="utf-8",
    )
    return target
