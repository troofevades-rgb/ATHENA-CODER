"""/memory — list, view, or delete persistent memories from the REPL.

R2 stage 3: reads + writes route through the profile-keyed provider
at ``(cfg.profile, agent.workspace)`` -- same coordinate the
foreground @tool surface (``tools/memory_tools.py``) and the agent's
system-prompt build (``agent/core.py``) use, so this command shows
what the agent will actually see.
"""

from __future__ import annotations

from .. import ui
from ..memory.store import delete_entry, list_entries, memory_dir, read_entry
from . import command


def _profile(agent) -> str:
    return agent.cfg.profile or "default"


@command("memory")
def cmd_memory(agent, arg: str = "") -> str:
    arg = arg.strip()
    sub, _, rest = arg.partition(" ")

    if not sub or sub == "list":
        entries = list_entries(_profile(agent), workspace=agent.workspace)
        d = memory_dir(_profile(agent), workspace=agent.workspace)
        if not entries:
            ui.info(f"no memories at {d}")
            return ""
        ui.console.print(f"[dim]dir: {d}[/]")
        for entry in entries:
            fname = entry.path.name if entry.path is not None else f"{entry.name}.md"
            ui.console.print(
                f"  [bold]{fname}[/]  [dim][{entry.type}][/]  {entry.name}"
            )
            if entry.description:
                ui.console.print(f"    [dim]{entry.description}[/]")
        return ""

    if sub == "show":
        if not rest:
            ui.error("usage: /memory show <filename>")
            return ""
        name = rest[:-3] if rest.endswith(".md") else rest
        entry = read_entry(_profile(agent), name, workspace=agent.workspace)
        if not entry:
            ui.error(f"not found: {rest}")
            return ""
        fname = entry.path.name if entry.path is not None else f"{entry.name}.md"
        ui.console.print(f"[bold]{fname}[/]  [dim][{entry.type}][/]")
        ui.console.print(f"  name: {entry.name}")
        ui.console.print(f"  description: {entry.description}")
        ui.console.print()
        ui.console.print(entry.body)
        return ""

    if sub == "delete":
        if not rest:
            ui.error("usage: /memory delete <filename>")
            return ""
        name = rest[:-3] if rest.endswith(".md") else rest
        if delete_entry(_profile(agent), name, workspace=agent.workspace):
            ui.info(f"deleted {rest}")
        else:
            ui.error(f"not found: {rest}")
        return ""

    if sub == "dir":
        ui.console.print(str(memory_dir(_profile(agent), workspace=agent.workspace)))
        return ""

    ui.error(f"unknown subcommand: {sub}. try: /memory [list|show <file>|delete <file>|dir]")
    return ""
