"""Tests for archive_skill / unarchive_skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from athena.skills.archive import SkillNotFoundError, archive_skill, unarchive_skill
from athena.skills.frontmatter import parse_frontmatter


def test_skill_delete_moves_to_archive(isolated_home: Path, write_skill) -> None:
    user_skills = isolated_home / ".athena" / "skills"
    user_skills.mkdir(parents=True)
    write_skill(user_skills, "to-archive")

    archived = archive_skill("to-archive")
    assert archived.parent.name == ".archive"
    assert not (user_skills / "to-archive").exists()

    fm, _ = parse_frontmatter(archived / "SKILL.md")
    assert fm.state == "archived"


def test_skill_unarchive_restores(isolated_home: Path, write_skill) -> None:
    user_skills = isolated_home / ".athena" / "skills"
    user_skills.mkdir(parents=True)
    write_skill(user_skills, "round")
    archive_skill("round")

    restored = unarchive_skill("round")
    assert restored.parent == user_skills
    fm, _ = parse_frontmatter(restored / "SKILL.md")
    assert fm.state == "active"


def test_archive_collision_renames(isolated_home: Path, write_skill) -> None:
    user_skills = isolated_home / ".athena" / "skills"
    user_skills.mkdir(parents=True)
    archive_dir = user_skills / ".archive"
    archive_dir.mkdir()

    write_skill(archive_dir, "collide", state="archived")
    write_skill(user_skills, "collide")

    new_path = archive_skill("collide")
    assert new_path.name == "collide-1"
    # Both archived copies exist
    assert (archive_dir / "collide").exists()
    assert (archive_dir / "collide-1").exists()


def test_archive_missing_skill_raises(isolated_home: Path) -> None:
    (isolated_home / ".athena" / "skills").mkdir(parents=True)
    with pytest.raises(SkillNotFoundError):
        archive_skill("nope")


def test_unarchive_non_archived_raises(isolated_home: Path, write_skill) -> None:
    user_skills = isolated_home / ".athena" / "skills"
    user_skills.mkdir(parents=True)
    write_skill(user_skills, "active-skill")
    with pytest.raises(SkillNotFoundError, match="not archived"):
        unarchive_skill("active-skill")


def test_archive_rolls_back_on_frontmatter_failure(
    isolated_home: Path,
    write_skill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_patch_state`` raises after the directory move, the move
    must be reversed -- otherwise the catalog ends up with a skill
    living under ``.archive/`` whose frontmatter still says
    ``state=active``, invisible to default ``discover_skills``."""
    user_skills = isolated_home / ".athena" / "skills"
    user_skills.mkdir(parents=True)
    write_skill(user_skills, "rollback-me")
    original = user_skills / "rollback-me"
    assert original.exists()

    import athena.skills.archive as archive_mod

    def _explode(*_a, **_kw):
        raise RuntimeError("simulated frontmatter write failure")

    monkeypatch.setattr(archive_mod, "_patch_state", _explode)

    with pytest.raises(RuntimeError, match="simulated"):
        archive_mod.archive_skill("rollback-me")

    # Directory must be back at its original location; .archive/ must
    # not contain a half-archived entry.
    assert original.exists(), (
        "archive_skill failed to roll back the move; skill is orphaned under .archive/"
    )
    archive_dir = user_skills / ".archive"
    if archive_dir.exists():
        assert not (archive_dir / "rollback-me").exists()
