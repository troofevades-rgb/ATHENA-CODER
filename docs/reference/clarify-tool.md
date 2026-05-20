# `clarify` tool

A multiple-choice prompt the agent can call when a request is
ambiguous between two or more plausible interpretations. Three
resolution paths, in priority order: background fork → instant
deny; gateway hook → platform-native prompt (Tier 4); foreground
stdin → numbered prompt.

## Why this exists

Without `clarify`, an ambiguous user request forces the agent to
guess. If the guess is wrong, the user has to correct it on the
next turn — a wasted round-trip. Asking once up front turns "ask,
guess, correct" into "ask, answer, do."

The tool is opt-in for the agent; the description tells it to use
`clarify` *sparingly* — only when guessing wrong would cost more
than asking. The agent shouldn't polite-by-default with this.

## Signature

```python
clarify(
    question: str,
    options: list[str],   # 2-4 short labels
    *,
    timeout_seconds: int = 300,
    allow_freeform: bool = False,
) -> str
```

Returns the resolved option text (or freeform text), or a
`"no answer received ..."` string on timeout / EOF / fork mode.

## Foreground behaviour

```
? When you say 'archive these', which scope?
  1. Just today's files
  2. This week's files
  3. Everything
Choose 1-3:
```

User input resolution (in priority order):

1. **Numeric in range** (`2`) → `options[n-1]`.
2. **Case-insensitive exact label** (`yes` matches `Yes`).
3. **Unique case-insensitive prefix** (`y` uniquely prefixes
   `Yes` against `["Yes", "No"]` → `Yes`).
4. **Pass-through** — return the raw input. Lets the agent
   handle "the user typed something unexpected" rather than the
   tool guessing.

Ambiguous prefixes (`a` against `["apple", "aardvark"]`) fall
through to pass-through.

## Background fork behaviour

When invoked from inside a fork (curator, auxiliary, background
review), the tool detects the fork via the `in_fork_context`
ContextVar (set by `athena/agent/fork.py:_runner`) and returns
immediately:

```
no answer received (background fork)
```

The agent sees this, the fork keeps moving. No blocking on stdin
the fork doesn't own anyway.

## Gateway behaviour (Tier 4)

Gateway adapters (Telegram, Discord, Slack) register a
`GatewayClarifyHook` via `athena.tools.clarify.register_gateway_hook`.
The tool calls `hook.resolve(question, options, timeout_seconds,
allow_freeform)` and:

- If the hook returns a string, that's the answer.
- If it returns `None`, fall through to stdin (e.g., gateway is
  installed but this particular session isn't bound to a chat
  adapter).
- If it raises, fall through to stdin and log a `WARNING`.

The hook is expected to honour `timeout_seconds` itself — the tool
doesn't wrap it in another timeout layer because that would require
a second thread / event loop in the sync call path.

Tier 4 ships the actual hook implementations (inline keyboards on
Telegram, button rows on Discord, block kit on Slack); v0.3.0
ships the contract.

## Configuration

```toml
clarify_default_timeout_seconds = 300   # 5 min — the user-walked-away case
clarify_allow_freeform = false          # default: numbered-only
```

Per-call args override the config defaults.

## Timeout behaviour

The stdin path runs the `input()` call on a daemon thread + Queue
so the timeout can fire even if the user stepped away. A daemon
thread blocked on `input()` past the timeout will eventually finish
on process exit — fine for a tool that isn't invoked in a tight
loop. The next `clarify` call gets a fresh thread.

## Logging

Every clarify call logs at INFO with the question (truncated to
~100 chars) and the resolved answer. Useful for training-data
review and replay.

## Implementation

- `athena/tools/clarify.py` — the tool + helpers.
- `athena/tools/__init__.py` — registers the tool on package
  import.
- `athena/agent/fork.py:_runner` — sets `in_fork_context.set(True)`
  before the fork's run_until_done, so the AUTO_DENY path fires.
- `athena/config.py` — `clarify_default_timeout_seconds` and
  `clarify_allow_freeform` defaults.
