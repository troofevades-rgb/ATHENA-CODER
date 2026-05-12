"""Slash command skills.

Each skill is a function that takes (agent, arg) and either:
  - returns None / continues normally
  - returns a string to inject as the next user message
  - returns False to exit the REPL

Skills are registered by name; '/foo arg' looks up 'foo' here.
"""
from __future__ import annotations
from typing import Callable

# (agent, arg) -> str | None | bool
SkillFn = Callable[..., object]

_SKILLS: dict[str, SkillFn] = {}


def skill(name: str):
    def deco(fn: SkillFn) -> SkillFn:
        _SKILLS[name] = fn
        return fn
    return deco


def get_skill(name: str) -> SkillFn | None:
    return _SKILLS.get(name)


def all_skills() -> dict[str, SkillFn]:
    return dict(_SKILLS)


# Import skill modules so they register
from . import init        # noqa: F401
from . import review      # noqa: F401
from . import loop        # noqa: F401
from . import compact     # noqa: F401
from . import resume      # noqa: F401
from . import memory_skill  # noqa: F401
from . import plan_skill   # noqa: F401
