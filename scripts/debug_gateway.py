"""Verbose gateway runner for diagnostics.

Same as ``athena gateway run`` but with discord.py + athena.gateway
loggers cranked up to DEBUG and a print on every inbound message
before any filtering. Useful when DMs aren't reaching the dispatch
pipeline and we need to know whether the event even arrived.
"""
from __future__ import annotations

import logging
import sys


def main() -> int:
    # Discord.py emits one INFO line per dispatched event at DEBUG.
    # Crank athena.gateway too so we see the daemon's view.
    for name in (
        "discord", "discord.gateway", "discord.client", "discord.http",
        "athena.gateway", "athena.gateway.base",
    ):
        logging.getLogger(name).setLevel(logging.DEBUG)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Wrap the discord adapter's _on_message so we see EVERY inbound
    # message before the filter runs. discord.py registers handlers by
    # __name__, so the wrapper MUST keep _on_message as its __name__
    # (functools.wraps does this) — otherwise discord.py registers a
    # listener under the wrong event name and the real handler never
    # fires.
    import functools
    from athena.gateway.platforms.discord import DiscordAdapter
    original = DiscordAdapter._on_message

    @functools.wraps(original)
    async def _on_message(self, message):  # name preserved for dispatch
        author = getattr(message.author, "name", "?")
        author_id = getattr(message.author, "id", "?")
        is_bot = getattr(message.author, "bot", False)
        content = getattr(message, "content", "") or ""
        channel_type = type(message.channel).__name__
        print(
            f"[DEBUG inbound] author={author!r} id={author_id} bot={is_bot} "
            f"channel={channel_type} content_len={len(content)} "
            f"content={content[:120]!r}",
            flush=True,
        )
        return await original(self, message)

    DiscordAdapter._on_message = _on_message

    # Delegate to the normal CLI runner.
    from athena.cli.gateway import main as gateway_main
    return gateway_main(["run"])


if __name__ == "__main__":
    sys.exit(main())
