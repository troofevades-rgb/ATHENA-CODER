"""Sub-agent dispatch — Claude Code's `Agent` tool.

A sub-agent is a fresh ocode Agent instance run on a scoped tool set with
its own system prompt. It runs the user-provided prompt to completion and
returns its final assistant message as a string.

Available subagent types:
- general-purpose: full tool set
- Explore: read-only — Read/Glob/Grep/WebFetch/WebSearch only
- Plan: planning-only — read-only tools, asked to produce a step-by-step plan
"""
from __future__ import annotations
from typing import Any

from .registry import tool, _REGISTRY, Tool
from .. import ui


SUBAGENT_TYPES = {
    "general-purpose": {
        "description": "General-purpose agent for complex multi-step research and tasks. Has access to the full tool set.",
        "scope": None,  # None = all tools
        "system_addendum": (
            "You are a sub-agent. Run the task to completion and return a single "
            "concise final message summarizing what you did and what you found. "
            "Do not ask the user for clarification — make reasonable judgment calls."
        ),
    },
    "Explore": {
        "description": "Read-only search agent for locating code, files, and answering 'where is X' questions.",
        "scope": {
            "Read", "read_file",
            "Glob", "glob",
            "Grep", "grep",
            "WebFetch", "web_fetch",
            "WebSearch", "web_search",
            "list_dir",
        },
        "system_addendum": (
            "You are an Explore sub-agent. You have READ-ONLY tools. Locate code, "
            "files, or symbols and report findings concisely. Do not propose edits "
            "or changes. Return the most relevant file paths with line numbers."
        ),
    },
    "Plan": {
        "description": "Planning agent that produces a step-by-step implementation plan without making changes.",
        "scope": {
            "Read", "read_file",
            "Glob", "glob",
            "Grep", "grep",
            "WebFetch", "web_fetch",
            "WebSearch", "web_search",
            "list_dir",
        },
        "system_addendum": (
            "You are a Plan sub-agent. You have READ-ONLY tools. Investigate enough "
            "to produce a concrete implementation plan: which files to touch, what to "
            "change, in what order, and what could go wrong. Return ONLY the plan."
        ),
    },
}


def _make_agent_factory():
    """Lazy import to avoid circular import (agent.py imports tools/, tools/ imports agent)."""
    from ..agent import Agent
    from ..config import Config
    return Agent, Config


@tool(
    name="Agent",
    description=(
        "Launch a sub-agent to handle a complex task. Each agent type has "
        "specific capabilities. Use general-purpose for open-ended research "
        "spanning many files. Use Explore for fast read-only code lookups. "
        "Use Plan to draft an implementation plan without making changes.\n\n"
        "Brief the sub-agent like a colleague who just walked in: state the "
        "goal, what you've ruled out, and what form of report you want. "
        "Self-contained prompts produce better results than terse commands."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short (3-5 word) task description."},
            "prompt": {"type": "string", "description": "The full task brief for the sub-agent."},
            "subagent_type": {
                "type": "string",
                "enum": list(SUBAGENT_TYPES.keys()),
                "description": "Which kind of sub-agent to spawn. Defaults to general-purpose.",
            },
        },
        "required": ["description", "prompt"],
    },
)
def Agent(description: str, prompt: str, subagent_type: str = "general-purpose") -> str:
    spec = SUBAGENT_TYPES.get(subagent_type)
    if not spec:
        return f"ERROR: unknown subagent_type {subagent_type!r}"

    AgentCls, ConfigCls = _make_agent_factory()

    # Inherit the parent agent's runtime context. We rely on file_ops._WORKSPACE
    # being set, since the parent has already configured it.
    from . import file_ops
    workspace = file_ops._WORKSPACE

    # Construct a config with disabled_tools set to anything outside the scope.
    cfg = ConfigCls()
    if spec["scope"] is not None:
        all_names = list(_REGISTRY.keys())
        cfg.disabled_tools = [n for n in all_names if n not in spec["scope"]]
    # Sub-agents always auto-approve their tools — they shouldn't pause for input.
    cfg.auto_approve_tools = True
    # Lean prompt for sub-agents to keep their context tight.
    cfg.lean_prompt = True

    # Inherit model/host from the live parent agent — load_config() would miss
    # --model CLI flags and /model overrides made mid-session.
    from .. import agent as agent_mod
    parent = agent_mod._CURRENT_AGENT
    if parent is not None:
        cfg.model = parent.model
        cfg.ollama_host = parent.cfg.ollama_host
    else:
        from ..config import load_config
        parent_cfg = load_config()
        cfg.model = parent_cfg.model
        cfg.ollama_host = parent_cfg.ollama_host

    ui.info(f"spawning sub-agent: {subagent_type} — {description}")
    sub = None
    try:
        sub = AgentCls(cfg, workspace)
        # Inject the addendum into the system prompt
        sub.messages[0]["content"] += "\n\n" + spec["system_addendum"]
        sub.run_turn(prompt)
        # Return the last assistant message as the result
        for m in reversed(sub.messages):
            if m.get("role") == "assistant":
                return m.get("content", "") or "(empty)"
        return "(no assistant response)"
    except Exception as e:
        return f"ERROR running sub-agent: {e}"
    finally:
        if sub is not None:
            try:
                sub.close()
            except Exception:
                pass
