"""Non-REPL athena subcommands (import-from-hermes, etc.).

``athena.__main__`` dispatches to handlers in this package when ``argv[1]``
matches a registered subcommand name; everything else falls through to the
interactive REPL.
"""
