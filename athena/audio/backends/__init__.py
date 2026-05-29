"""Audio transcription backends — one file per vendor.

Importing this package side-effect-registers any adapter
whose module is imported. The default in-tree adapter is
:mod:`athena.audio.backends.faster_whisper_local` (Whisper-
class, local, CPU/GPU-flexible, on-device). Real cloud
adapters land alongside one per file when needed.
"""

from __future__ import annotations

# Trigger registration — the backend's @register_provider runs
# at import time. Wrapped in try/except so a missing optional
# dep (faster-whisper not installed) doesn't break the rest of
# athena; the broker simply finds no audio backend and
# audio_analyze surfaces "no backend configured" cleanly.
try:
    from . import faster_whisper_local as _fw  # noqa: F401
except Exception:  # noqa: BLE001 — defensive at import time
    import logging

    logging.getLogger(__name__).debug(
        "faster_whisper_local backend not loaded (faster-whisper missing or import failed)",
        exc_info=True,
    )
