"""Profile lifecycle management.

Functions for the ``athena profile`` CLI surface:

- :func:`list_profiles` — every profile present on disk, sorted.
- :func:`create_profile` — make a new profile, optionally cloning
  another one's contents.
- :func:`delete_profile` — remove a profile (with confirmation token
  to prevent typos turning into data loss).
- :func:`switch_profile` — write to ``active_profile`` so the next
  invocation lands there.
- :func:`rename_profile` — move ``old`` to ``new``; updates
  ``active_profile`` if it pointed at ``old``.

Every function operates on the user-scope ``PROFILES_DIR``. None of
them touch profile contents — they manipulate the directory itself.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .resolution import (
    ACTIVE_PROFILE_FILE,
    DEFAULT_PROFILE,
    PROFILES_DIR,
    clear_active_profile_file,
    ensure_profile,
    is_valid_profile_name,
    profile_dir,
    profile_exists,
    set_active_profile_file,
)

logger = logging.getLogger(__name__)


# Default config.toml content seeded into new profiles. NOTE:
# athena currently loads ONLY the global ~/.athena/config.toml
# (config.load_config reads CONFIG_PATH); this per-profile file is
# reserved for a future per-profile override layer and is not read
# yet. The seeded comment must not promise otherwise — an earlier
# version did, and paired with the flat-layout migration moving the
# global config here, users' settings silently reset to defaults.
_DEFAULT_CONFIG_TOML = """\
# Reserved for profile-specific overrides (NOT YET LOADED).
# athena currently reads only the global ~/.athena/config.toml.
# This file exists so a future per-profile config layer has an
# obvious home; editing it today has no effect.
"""


def list_profiles() -> list[str]:
    """Return every existing profile directory, sorted."""
    if not PROFILES_DIR.exists():
        return []
    return sorted(
        entry.name
        for entry in PROFILES_DIR.iterdir()
        if entry.is_dir() and is_valid_profile_name(entry.name)
    )


def create_profile(name: str, *, copy_from: str | None = None) -> Path:
    """Create a new profile.

    If ``copy_from`` is given, every file under that profile is
    duplicated into the new one (config + skills + memory + sessions
    + everything). Otherwise the new profile gets the bootstrap
    layout from :func:`ensure_profile` plus a default config.toml.

    Raises ``ValueError`` for invalid names,
    ``FileExistsError`` when the name already exists, and
    ``FileNotFoundError`` when ``copy_from`` doesn't exist.
    """
    if not is_valid_profile_name(name):
        raise ValueError(
            f"invalid profile name: {name!r} (lowercase alphanumerics + _ - only, max 64 chars)"
        )
    dest = profile_dir(name)
    if dest.exists():
        raise FileExistsError(f"profile already exists: {name}")

    if copy_from is not None:
        if not is_valid_profile_name(copy_from):
            raise ValueError(f"invalid source profile name: {copy_from!r}")
        src = profile_dir(copy_from)
        if not src.exists():
            raise FileNotFoundError(f"source profile not found: {copy_from}")
        shutil.copytree(src, dest)
    else:
        ensure_profile(name)
        cfg = dest / "config.toml"
        if not cfg.exists():
            cfg.write_text(_DEFAULT_CONFIG_TOML, encoding="utf-8")
    return dest


def delete_profile(name: str, confirm_token: str) -> None:
    """Remove ``name`` from disk.

    Refuses to delete ``"default"`` (it's auto-created and serves as
    the fallback when no other profile is active). Requires
    ``confirm_token`` to match ``name`` to prevent typos from wiping
    a profile.

    Idempotent on missing profiles — if ``name`` doesn't exist,
    returns cleanly (callers don't usually want a stack trace from
    "delete a thing that's already gone").
    """
    if not is_valid_profile_name(name):
        raise ValueError(f"invalid profile name: {name!r}")
    if name == DEFAULT_PROFILE:
        raise ValueError(
            f"cannot delete the {DEFAULT_PROFILE!r} profile (auto-created; serves as the fallback)"
        )
    if confirm_token != name:
        raise ValueError(
            "confirmation token must equal the profile name "
            f"(got {confirm_token!r}, expected {name!r})"
        )
    target = profile_dir(name)
    if not target.exists():
        return
    shutil.rmtree(target)
    # If active_profile pointed at the deleted one, clear it so the
    # next invocation falls through to default.
    if (
        ACTIVE_PROFILE_FILE.exists()
        and ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip() == name
    ):
        clear_active_profile_file()


def switch_profile(name: str) -> None:
    """Mark ``name`` as the active profile.

    Writes ``~/.athena/active_profile`` so subsequent invocations
    (without ``--profile`` or ``ATHENA_PROFILE``) land here.

    Raises ``FileNotFoundError`` if the profile doesn't exist —
    silently writing an invalid active_profile would surface as
    confusing "profile not found" errors at next launch.
    """
    if not profile_exists(name):
        raise FileNotFoundError(f"profile not found: {name}")
    set_active_profile_file(name)


def rename_profile(old: str, new: str) -> None:
    """Move profile ``old`` to ``new``.

    Refuses to rename ``"default"`` (same rationale as delete).
    Refuses if ``new`` already exists. Updates ``active_profile`` if
    it pointed at ``old`` so the user's effective profile stays the
    same after the rename.
    """
    if not is_valid_profile_name(old) or not is_valid_profile_name(new):
        raise ValueError(f"invalid profile name(s): old={old!r}, new={new!r}")
    if old == DEFAULT_PROFILE:
        raise ValueError(f"cannot rename the {DEFAULT_PROFILE!r} profile")
    if not profile_exists(old):
        raise FileNotFoundError(f"profile not found: {old}")
    dest = profile_dir(new)
    if dest.exists():
        raise FileExistsError(f"profile already exists: {new}")
    profile_dir(old).rename(dest)
    if (
        ACTIVE_PROFILE_FILE.exists()
        and ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip() == old
    ):
        set_active_profile_file(new)
