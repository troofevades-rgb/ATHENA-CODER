"""False-refusal recovery (runtime.py).

When a turn makes ZERO tool calls and the model's reply looks like a
POLICY refusal of the request — not a concrete task blocker — athena
re-prompts it (cfg.refusal_reprompt_attempts times) with a truthful
reframe (this is the operator's own local project; routine dev help is
in scope), rather than silently surfacing a spurious refusal. The model
can still decline after the nudge. These tests pin that recovery via a
scripted stub provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from athena.agent.core import Agent
from athena.config import Config
from athena.providers.base import StreamChunk

_DONE = "Done: the bot's message handler is fixed and connects fine."
_REFUSE = "I'm sorry, but I can't help with creating that bot."
_BLOCKER = "I can't find the handler file you mentioned at that path."


class _ScriptedProvider:
    """Yields a scripted content string per model call (no tool calls).
    The last script repeats if the loop calls more times than scripted."""

    name = "scripted"
    requires_api_key = False

    def __init__(self, scripts: list[str]) -> None:
        self.scripts = scripts
        self.calls = 0

    def stream_chat(self, **kwargs: Any) -> Iterator[StreamChunk]:
        idx = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        yield StreamChunk("content", self.scripts[idx])
        yield StreamChunk("end", None)

    def parse_tool_calls(self, content: str, raw_response: dict) -> tuple:
        return content, []

    def list_models(self) -> list[str]:
        return ["scripted"]

    def show_model(self, model: str) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        return None


def _nudges(agent: Agent) -> list[dict]:
    return [
        m
        for m in agent.messages
        if m.get("role") == "user" and "[athena]" in (m.get("content") or "")
    ]


def test_refusal_triggers_one_reframe(isolated_home: Path, workspace: Path) -> None:
    """A policy refusal on round 1 → a reframing nudge is injected and
    the model re-streams; round 2 proceeds. Two model calls, one nudge
    whose text reframes the work as legitimate."""
    cfg = Config(model="scripted", refusal_reprompt_attempts=1)
    provider = _ScriptedProvider([_REFUSE, _DONE])
    agent = Agent(cfg, workspace, provider=provider)

    agent.run_turn("help me fix my telegram bot")

    assert provider.calls == 2, "model should have been re-prompted once"
    nudges = _nudges(agent)
    assert len(nudges) == 1
    assert "legitimate" in nudges[0]["content"].lower()
    assert "own local project" in nudges[0]["content"].lower()


def test_refusal_recovery_disabled_when_zero(isolated_home: Path, workspace: Path) -> None:
    """refusal_reprompt_attempts=0 surfaces the refusal as-is — one call,
    no nudge (operators who want the raw refusal can opt out)."""
    cfg = Config(model="scripted", refusal_reprompt_attempts=0)
    provider = _ScriptedProvider([_REFUSE, _DONE])
    agent = Agent(cfg, workspace, provider=provider)

    agent.run_turn("help me fix my telegram bot")

    assert provider.calls == 1
    assert _nudges(agent) == []


def test_concrete_blocker_is_not_reframed(isolated_home: Path, workspace: Path) -> None:
    """A real task blocker ('I can't find the file') is NOT a refusal —
    no nudge, so the loop doesn't spin on a genuine limitation."""
    cfg = Config(model="scripted", refusal_reprompt_attempts=1)
    provider = _ScriptedProvider([_BLOCKER])
    agent = Agent(cfg, workspace, provider=provider)

    agent.run_turn("update the handler")

    assert provider.calls == 1
    assert _nudges(agent) == []


def test_refusal_recovery_is_bounded(isolated_home: Path, workspace: Path) -> None:
    """If the model refuses on EVERY call, the reframe fires at most
    refusal_reprompt_attempts times, then the refusal is accepted — no
    infinite loop."""
    cfg = Config(model="scripted", refusal_reprompt_attempts=1, max_turn_steps=25)
    provider = _ScriptedProvider([_REFUSE])  # always refuses
    agent = Agent(cfg, workspace, provider=provider)

    agent.run_turn("help me fix my telegram bot")

    # call 1 refuses → nudge; call 2 refuses → out of nudges → accept.
    assert provider.calls == 2
    assert len(_nudges(agent)) == 1
