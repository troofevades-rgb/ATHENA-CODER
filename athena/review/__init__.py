"""Per-turn background review.

Fires a daemon-thread fork after every N tool calls (default 10) to consider
whether to write to memory or patch a skill based on the just-completed turn.
The user has already received their response; review happens in the
background and never blocks the next turn.
"""
