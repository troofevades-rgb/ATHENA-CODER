"""Install-method detection (T6-07.1).

athena can be installed four ways; ``athena update`` needs to
pick the right upgrade path per host:

  pip       — ``pip install athena-coder`` (PyPI release)
  pipx      — ``pipx install athena-coder`` (isolated venv)
  git       — ``git clone`` + ``pip install .`` (source build)
  editable  — ``pip install -e .`` (developer install)
  unknown   — none of the above match; the command surfaces
              this cleanly and exits

The detection signal layers:

  1. The on-disk path of the installed ``athena`` package
     (file we're running from). Containing ``pipx`` →
     :class:`InstallMethod.PIPX`. Containing a git repo
     ancestor → :class:`InstallMethod.GIT` (or EDITABLE).
  2. ``importlib.metadata.PackageNotFoundError`` → UNKNOWN.
  3. ``direct_url.json`` ``dir_info.editable=True`` → EDITABLE.

The package name on PyPI is ``athena-coder`` (the project's
``pyproject.toml`` ``[project] name``). All detection /
upgrade calls use that string.

Pure detection — no I/O beyond reading metadata files; no
network. Tests monkeypatch the metadata + path lookups.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import pathlib
from enum import Enum

logger = logging.getLogger(__name__)


PACKAGE_NAME = "athena-coder"


class InstallMethod(str, Enum):
    """How athena was installed on this host."""

    PIP = "pip"
    PIPX = "pipx"
    GIT = "git"
    EDITABLE = "editable"
    UNKNOWN = "unknown"


def detect(*, pkg: str = PACKAGE_NAME) -> InstallMethod:
    """Return the :class:`InstallMethod` for the running
    install.

    Order of checks (first match wins):

      1. EDITABLE — package metadata's ``direct_url.json`` has
         ``dir_info.editable=True``. This is what
         ``pip install -e .`` writes. Even when the directory
         is also a git repo, EDITABLE is the right answer (the
         user is iterating on a source checkout; updating via
         ``git pull`` would surprise them with rebuilt files).
      2. GIT — the package path lives under a git repository
         and isn't editable. Source-build install that wants
         ``git pull`` + reinstall on upgrade.
      3. PIPX — the package path contains ``pipx`` (the
         pipx-managed venv lives under ``~/.local/pipx/...``).
      4. PIP — the package is registered in importlib.metadata
         but none of the above match. Treat as a regular PyPI
         install.
      5. UNKNOWN — metadata lookup failed entirely (probably
         an in-place dev checkout that was never installed).

    Defensive: any unexpected exception in the detection
    helpers falls through to UNKNOWN — a malformed
    ``direct_url.json`` doesn't crash athena's startup.
    """
    pkg_path = _package_root()

    # 1. Editable wins over git when both signals are present.
    if _is_editable(pkg):
        return InstallMethod.EDITABLE

    # 2. Git repo ancestor.
    if _find_git_root(pkg_path) is not None:
        return InstallMethod.GIT

    # 3. pipx — characteristic path.
    if _looks_like_pipx(pkg_path):
        return InstallMethod.PIPX

    # 4. Plain PyPI install (or missing metadata → UNKNOWN).
    try:
        importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return InstallMethod.UNKNOWN
    return InstallMethod.PIP


# ---------------------------------------------------------------------------
# Helpers — every one defensive; an unexpected failure falls through
# ---------------------------------------------------------------------------


def _package_root() -> pathlib.Path:
    """Filesystem path of the athena package directory.
    Independent module so tests can monkeypatch a different
    location without messing with __file__ resolution."""
    return pathlib.Path(__file__).resolve().parent.parent


def _find_git_root(start: pathlib.Path) -> pathlib.Path | None:
    """Walk up from ``start`` looking for a ``.git`` dir or a
    bare-repo ``HEAD``+``refs`` pair. Returns the repo root or
    None.

    Stops at the filesystem root so a misconfigured drive
    doesn't infinite-loop. Symlinks are NOT followed for the
    walk — we want the real path's ancestors."""
    try:
        cur = pathlib.Path(start).resolve()
    except OSError:
        return None
    while True:
        if (cur / ".git").exists():
            return cur
        # Bare-repo shape (uncommon for installs but cheap to
        # check).
        if (cur / "HEAD").exists() and (cur / "refs").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def _is_editable(pkg: str) -> bool:
    """``True`` iff the package metadata records an editable
    install. ``direct_url.json`` lives in the dist-info and
    its ``dir_info.editable`` field is what
    ``pip install -e .`` plants.

    Any read failure / malformed JSON → False (we conclude
    "not editable" rather than crash; the worst-case
    consequence is offering a git/pip path to an editable
    install which the apply step's own gate then catches)."""
    try:
        dist = importlib.metadata.distribution(pkg)
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001
        logger.debug("editable check: distribution lookup failed: %s", e)
        return False
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return False
    dir_info = payload.get("dir_info") if isinstance(payload, dict) else None
    if not isinstance(dir_info, dict):
        return False
    return bool(dir_info.get("editable", False))


def _looks_like_pipx(path: pathlib.Path) -> bool:
    """Heuristic: pipx venvs live under directories containing
    ``pipx`` in the path (``~/.local/pipx/venvs/...`` on
    POSIX, ``%LOCALAPPDATA%\\pipx\\...`` on Windows). The
    string search is case-insensitive to handle both."""
    try:
        s = str(path).lower()
    except Exception:  # noqa: BLE001
        return False
    return "pipx" in s
