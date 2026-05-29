"""Install-method detection tests (T6-07.1).

Detection layers four signals (editable metadata, git repo
ancestry, pipx-in-path heuristic, package metadata presence)
and picks the right :class:`InstallMethod`. Tests monkeypatch
each signal independently so detection logic is exercised
without modifying the real install.
"""

from __future__ import annotations

import importlib.metadata
import json
import pathlib
import sys
from pathlib import Path

import pytest

# athena/update/__init__.py re-exports `detect` as the FUNCTION
# which shadows the submodule attribute; pull the module out of
# sys.modules directly so monkeypatch can target it.
import athena.update.detect  # noqa: F401 — load the submodule

detect_module = sys.modules["athena.update.detect"]
from athena.update.detect import InstallMethod, detect

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeDistribution:
    """Minimal stand-in for importlib.metadata.Distribution
    with a ``read_text("direct_url.json")`` payload."""

    def __init__(self, *, direct_url: dict | None = None):
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        if name == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


def _patch_metadata(
    monkeypatch,
    *,
    found: bool = True,
    distribution: _FakeDistribution | None = None,
) -> None:
    """Wire importlib.metadata to a stub. ``found=False``
    raises PackageNotFoundError on both lookups (the UNKNOWN
    path)."""

    def _version(_pkg):
        if not found:
            raise importlib.metadata.PackageNotFoundError(_pkg)
        return "0.2.0"

    def _distribution(_pkg):
        if not found:
            raise importlib.metadata.PackageNotFoundError(_pkg)
        return distribution or _FakeDistribution()

    monkeypatch.setattr(detect_module.importlib.metadata, "version", _version)
    monkeypatch.setattr(detect_module.importlib.metadata, "distribution", _distribution)


# ---------------------------------------------------------------------------
# pip — vanilla PyPI install
# ---------------------------------------------------------------------------


def test_detect_pip(monkeypatch, tmp_path: Path):
    """Vanilla install: metadata present, not editable, no git
    ancestor, path doesn't contain 'pipx'."""
    monkeypatch.setattr(
        detect_module, "_package_root", lambda: tmp_path / "site-packages" / "athena"
    )
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.PIP


def test_detect_pip_with_direct_url_not_editable(monkeypatch, tmp_path: Path):
    """direct_url.json present but ``dir_info.editable=False``
    — wheel install with a URL recorded but not editable. Still
    PIP."""
    dist = _FakeDistribution(
        direct_url={"url": "https://pypi.org/...", "dir_info": {"editable": False}}
    )
    monkeypatch.setattr(detect_module, "_package_root", lambda: tmp_path / "athena")
    _patch_metadata(monkeypatch, found=True, distribution=dist)
    assert detect() == InstallMethod.PIP


# ---------------------------------------------------------------------------
# pipx — path-based heuristic
# ---------------------------------------------------------------------------


def test_detect_pipx(monkeypatch, tmp_path: Path):
    """Path contains 'pipx' → PIPX (case-insensitive)."""
    pipx_path = tmp_path / "pipx" / "venvs" / "athena-coder" / "lib" / "athena"
    monkeypatch.setattr(detect_module, "_package_root", lambda: pipx_path)
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.PIPX


def test_detect_pipx_case_insensitive(monkeypatch, tmp_path: Path):
    """Windows pipx lives under %LOCALAPPDATA%\\pipx — the
    detection is case-insensitive."""
    path = tmp_path / "PIPX" / "venvs" / "athena"
    monkeypatch.setattr(detect_module, "_package_root", lambda: path)
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.PIPX


# ---------------------------------------------------------------------------
# git — repo-ancestor detection
# ---------------------------------------------------------------------------


def test_detect_git(monkeypatch, tmp_path: Path):
    """Package path lives under a directory with a .git dir →
    GIT. Editable metadata absent."""
    repo = tmp_path / "athena-checkout"
    (repo / ".git").mkdir(parents=True)
    pkg_path = repo / "athena"
    pkg_path.mkdir()
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.GIT


def test_detect_git_bare_repo_shape(monkeypatch, tmp_path: Path):
    """A bare-repo ancestor (HEAD + refs/) is also recognised."""
    repo = tmp_path / "athena-bare"
    repo.mkdir()
    (repo / "HEAD").write_text("ref: refs/heads/main")
    (repo / "refs").mkdir()
    pkg_path = repo / "athena"
    pkg_path.mkdir()
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.GIT


def test_detect_git_walks_up_to_find_repo(monkeypatch, tmp_path: Path):
    """The walk-up finds .git two levels above the package."""
    repo = tmp_path / "wrap"
    (repo / ".git").mkdir(parents=True)
    pkg_path = repo / "src" / "athena"
    pkg_path.mkdir(parents=True)
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=_FakeDistribution())
    assert detect() == InstallMethod.GIT


# ---------------------------------------------------------------------------
# editable — direct_url.json beats git ancestor
# ---------------------------------------------------------------------------


def test_detect_editable(monkeypatch, tmp_path: Path):
    """direct_url.json with dir_info.editable=True → EDITABLE.
    This wins over a git-repo ancestor (the user is iterating
    on a source checkout; we don't want to git-pull behind
    them)."""
    repo = tmp_path / "athena-checkout"
    (repo / ".git").mkdir(parents=True)
    pkg_path = repo / "athena"
    pkg_path.mkdir()
    dist = _FakeDistribution(
        direct_url={
            "url": f"file://{repo}",
            "dir_info": {"editable": True},
        }
    )
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=dist)
    assert detect() == InstallMethod.EDITABLE


def test_detect_editable_without_git_ancestor(monkeypatch, tmp_path: Path):
    """Editable install without a git ancestor (rare — pip
    install -e on a non-repo tree)."""
    pkg_path = tmp_path / "non-repo" / "athena"
    pkg_path.mkdir(parents=True)
    dist = _FakeDistribution(direct_url={"url": "file://x", "dir_info": {"editable": True}})
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=dist)
    assert detect() == InstallMethod.EDITABLE


# ---------------------------------------------------------------------------
# unknown — missing metadata
# ---------------------------------------------------------------------------


def test_detect_unknown(monkeypatch, tmp_path: Path):
    """importlib.metadata raises PackageNotFoundError + no git
    ancestor + no pipx-in-path → UNKNOWN. This is the
    in-place-source-checkout-never-installed case."""
    monkeypatch.setattr(detect_module, "_package_root", lambda: tmp_path / "athena")
    _patch_metadata(monkeypatch, found=False)
    assert detect() == InstallMethod.UNKNOWN


# ---------------------------------------------------------------------------
# Defensive — malformed direct_url.json doesn't crash
# ---------------------------------------------------------------------------


def test_detect_malformed_direct_url_doesnt_crash(monkeypatch, tmp_path: Path):
    """A corrupt direct_url.json falls through to "not
    editable" — the worst-case downstream consequence is
    offering a pip/git path to an editable install, which the
    apply step's own gate handles."""

    class _CorruptDist(_FakeDistribution):
        def read_text(self, name: str) -> str | None:
            if name == "direct_url.json":
                return "not valid json {{{"
            return None

    repo = tmp_path / "athena-checkout"
    (repo / ".git").mkdir(parents=True)
    pkg_path = repo / "athena"
    pkg_path.mkdir()
    monkeypatch.setattr(detect_module, "_package_root", lambda: pkg_path)
    _patch_metadata(monkeypatch, found=True, distribution=_CorruptDist())
    # Falls through to GIT (editable check returned False
    # because of the parse error).
    assert detect() == InstallMethod.GIT


def test_detect_missing_direct_url_is_not_editable(monkeypatch, tmp_path: Path):
    """No direct_url.json at all → not editable (the common
    wheel-install case)."""

    class _NoDirectUrl(_FakeDistribution):
        def read_text(self, name: str) -> str | None:
            return None

    monkeypatch.setattr(detect_module, "_package_root", lambda: tmp_path / "athena")
    _patch_metadata(monkeypatch, found=True, distribution=_NoDirectUrl())
    assert detect() == InstallMethod.PIP


# ---------------------------------------------------------------------------
# _find_git_root helper
# ---------------------------------------------------------------------------


def test_find_git_root_returns_none_when_no_repo(tmp_path: Path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert detect_module._find_git_root(deep) is None


def test_find_git_root_walks_up(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "src" / "athena" / "update"
    deep.mkdir(parents=True)
    assert detect_module._find_git_root(deep) == repo.resolve()
