"""athena v2 file-based skill format.

Skills are folders under ``~/.athena/skills/`` and ``<workspace>/.athena/skills/``.
Each folder contains a ``SKILL.md`` with YAML frontmatter (agentskills.io
standard plus athena v2 lifecycle fields), and optional ``references/``,
``templates/``, and ``scripts/`` subdirectories.

See ``athena/skills/frontmatter.py`` for the SkillFrontmatter dataclass and
parse/serialize functions.
"""
