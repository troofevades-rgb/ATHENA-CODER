"""Tool registry. Tools register themselves via @tool() and expose:
   - a name
   - a JSON schema description (Ollama function-call format)
   - a Python callable that takes kwargs from the model and returns a string

The registry produces the `tools` array we send to Ollama and dispatches calls.
"""
from __future__ import annotations
import inspect
import json
import traceback
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]
    # If true, the UI will ask the user to confirm before running.
    requires_confirmation: bool = False


_REGISTRY: dict[str, Tool] = {}


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    requires_confirmation: bool = False,
    aliases: list[str] | None = None,
):
    """Decorator to register a function as a tool.

    `aliases` registers additional tool names that dispatch to the same function.
    Useful for compatibility (e.g. exposing both 'Read' and 'read_file').
    Aliases are NOT included in the schema sent to the model — only the canonical
    name is, to avoid confusing the model with duplicates.
    """
    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        t = Tool(
            name=name,
            description=description,
            parameters=parameters,
            func=fn,
            requires_confirmation=requires_confirmation,
        )
        _REGISTRY[name] = t
        for alias in aliases or []:
            _ALIASES[alias] = name
        return fn
    return deco


_ALIASES: dict[str, str] = {}


def resolve_alias(name: str) -> str:
    return _ALIASES.get(name, name)


def get_tool(name: str) -> Tool | None:
    canonical = _ALIASES.get(name, name)
    return _REGISTRY.get(canonical)


def all_tools(disabled: list[str] | None = None) -> list[Tool]:
    disabled = disabled or []
    return [t for n, t in _REGISTRY.items() if n not in disabled]


def ollama_schema(disabled: list[str] | None = None) -> list[dict[str, Any]]:
    """Build the tools[] array Ollama expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in all_tools(disabled)
    ]


def dispatch(name: str, arguments: Any) -> str:
    """Call a tool by name with arguments dict (or JSON string).
    Returns the string result that goes back to the model.
    Catches exceptions so the agent can keep going.
    Names matching an alias are resolved to the canonical tool.
    """
    t = get_tool(name)
    if not t:
        return f"ERROR: unknown tool '{name}'"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return f"ERROR: arguments to '{name}' were not valid JSON: {arguments!r}"
    if not isinstance(arguments, dict):
        return f"ERROR: arguments to '{name}' must be an object, got {type(arguments).__name__}"
    # Filter out kwargs the function doesn't accept (defensive — small models hallucinate keys).
    # If the function accepts **kwargs (VAR_KEYWORD), pass everything through; this is the case
    # for MCP-bridged tools where validation happens server-side.
    sig = inspect.signature(t.func)
    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_var_kw:
        valid = dict(arguments)
    else:
        valid = {k: v for k, v in arguments.items() if k in sig.parameters}
    try:
        result = t.func(**valid)
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
        return result
    except Exception as e:
        # Log the full traceback locally; only return a one-line error to the
        # model. Tracebacks confuse small models and bloat conversation context.
        import sys
        print(f"[tool {name}] {traceback.format_exc()}", file=sys.stderr)
        return f"ERROR running {name}: {type(e).__name__}: {e}"
