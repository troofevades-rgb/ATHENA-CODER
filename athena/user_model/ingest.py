"""Shared trigger for post-event user-model fact extraction.

Both ``/compact`` and session close fire ``ingest_session`` on a
detached daemon thread when the user-model backend is enabled and the
trigger's config flag is on. Centralised here so the two call sites
can't drift (``ingest_on_session_end`` was declared but never wired
until this module existed).
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Map a trigger name to the ``UserModelConfig`` flag that gates it.
_FLAG_BY_TRIGGER = {
    "compact": "ingest_on_compact",
    "session_end": "ingest_on_session_end",
}


def maybe_fire_ingest(
    agent: Any,
    transcript: list[dict[str, Any]],
    *,
    trigger: str,
) -> bool:
    """Kick off ``ingest_session`` on a daemon thread when the user-model
    backend is enabled and the trigger's flag is on.

    Fire-and-forget: returns ``True`` if a worker was started, ``False``
    if a gate said no. Every error inside the worker is swallowed so a
    misbehaving extractor never reaches the user.
    """
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return False
    um = getattr(cfg, "user_model", None)
    if um is None:
        return False
    flag = _FLAG_BY_TRIGGER.get(trigger)
    if flag is None or not getattr(um, flag, False):
        return False
    if getattr(um, "backend", None) in ("none", "", None):
        return False
    if not transcript:
        return False

    def _worker() -> None:
        import asyncio

        try:
            from ..tools import file_ops
            from ..tools.memory_query_tool import _build_llm_call
            from . import get_user_model_backend
        except ImportError:
            return
        try:
            backend = get_user_model_backend(
                cfg,
                llm_call=_build_llm_call(agent),
                workspace=file_ops._WORKSPACE,
            )
        except (ValueError, NotImplementedError):
            return
        session_id = getattr(agent, "session_id", None) or uuid.uuid4().hex
        try:
            asyncio.run(backend.ingest_session(transcript, session_id=session_id))
        except Exception:  # noqa: BLE001 — fire-and-forget
            logger.debug("user-model %s ingest failed", trigger, exc_info=True)

    threading.Thread(
        target=_worker,
        name="athena-user-model-ingest",
        daemon=True,
    ).start()
    return True
