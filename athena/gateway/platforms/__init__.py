"""Platform adapters for the gateway daemon.

Each module in this package implements one platform — Telegram,
Slack, Discord, … — by subclassing
:class:`athena.gateway.base.GatewayAdapter`. The base handles every
reliability concern (stale-lock heal, pending-merge, bypass-command
routing, interrupt-on-text); platform modules only own the wire
protocol and the platform-specific approval UI.

Modules are imported lazily by the CLI so a headless install (without
the optional ``gateway`` extra) doesn't fail at startup if the SDKs
are missing.
"""
