"""Process-wide aiohttp DNS-resolver setup.

aiohttp selects the aiodns/c-ares ``AsyncResolver`` by default whenever
``aiodns`` is importable. On some Windows hosts c-ares cannot read the
system DNS configuration, so every lookup fails ("Could not contact DNS
servers") even though the OS resolver works fine -- which leaves every
aiohttp-based client (the gateway adapters, the ``athena proxy`` endpoint,
the webhook listener) unable to connect.

:func:`ensure_working_dns_resolver` detects that case and falls back to
aiohttp's ``ThreadedResolver`` (``socket.getaddrinfo`` via the OS). It is a
no-op on healthy hosts, on non-Windows (the c-ares bug is Windows-specific),
and when aiohttp isn't installed. Call it once at sync process startup,
before any event loop runs.
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger("athena.net")

# Neutral, highly-available canaries. We only fall back when DNS resolution
# fails for ALL of them, so one host being unreachable for non-DNS reasons
# doesn't trigger a needless (slower) ThreadedResolver switch.
_PROBE_HOSTS: tuple[str, ...] = ("cloudflare.com", "google.com")


def _install_threaded_resolver() -> None:
    """Force aiohttp's default resolver to ``ThreadedResolver`` in both the
    ``resolver`` and ``connector`` modules. The connector binds its own
    ``DefaultResolver`` name at import, so the default-connector path used by
    discord.py / aiogram / slack-sdk needs it patched too."""
    import aiohttp.resolver

    aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
    import aiohttp.connector

    # ``aiohttp.connector.DefaultResolver`` is an internal binding -- guard so a
    # future aiohttp that drops it can't break us.
    if hasattr(aiohttp.connector, "DefaultResolver"):
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver


def _aiodns_can_resolve() -> bool:
    """Probe the aiodns ``AsyncResolver`` against the canary hosts. Returns
    True if any resolves, False if all fail. Returns True (assume healthy)
    when an event loop is already running, since the sync probe can't run
    then -- callers are expected to invoke the public function at sync
    startup, before the loop."""
    import aiohttp.resolver

    try:
        asyncio.get_running_loop()
        return True  # inside a running loop: can't probe, don't false-fallback
    except RuntimeError:
        pass

    async def _probe() -> bool:
        resolver = aiohttp.resolver.AsyncResolver()
        try:
            for host in _PROBE_HOSTS:
                try:
                    await resolver.resolve(host, 443)
                    return True
                except Exception:
                    continue
            return False
        finally:
            await resolver.close()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


def ensure_working_dns_resolver(mode: str = "auto") -> None:
    """Set aiohttp's default DNS resolver according to ``mode``:

    - ``"async"``    -- leave aiodns/AsyncResolver as-is (no probe).
    - ``"threaded"`` -- force ThreadedResolver unconditionally.
    - ``"auto"`` (default) -- on Windows, if aiodns is the default but can't
      resolve DNS, fall back to ThreadedResolver; otherwise leave it. No-op on
      non-Windows and when aiodns isn't the default.

    Idempotent, and safe when aiohttp isn't installed. Call once at sync
    process startup, before any event loop runs. Unknown ``mode`` values are
    treated as ``"auto"``.
    """
    try:
        import aiohttp.resolver as _resolver
    except ImportError:
        return  # headless install without aiohttp -- nothing to do

    if mode == "async":
        return
    if mode == "threaded":
        _install_threaded_resolver()
        return

    # "auto" (and any unrecognized value falls through to here)
    if _resolver.DefaultResolver is not _resolver.AsyncResolver:
        return  # aiodns absent or already overridden
    if sys.platform != "win32":
        return  # the c-ares-can't-read-system-DNS failure is Windows-specific
    if _aiodns_can_resolve():
        return
    logger.warning(
        "aiodns/c-ares resolver can't resolve DNS on this host; "
        "falling back to aiohttp ThreadedResolver (OS resolver)"
    )
    _install_threaded_resolver()
