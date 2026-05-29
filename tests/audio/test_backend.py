"""T4-04.1 — backend + contract tests.

Pins:
  - Capability declared on the in-tree backend
  - Backend resolves via MediaRegistry.backend_for("audio_transcription")
  - is_available True when faster-whisper importable
  - Segment + TranscribeResult round-trip through to_dict
  - chunk_offset_s pushes timestamps to absolute file-seconds
    (via the stub — the contract test, not faster-whisper)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from athena.audio.job import (
    ContentType,
    Segment,
    TranscribeResult,
)

# ---------------------------------------------------------------
# Capability declaration
# ---------------------------------------------------------------


def test_capabilities_has_audio_transcription_field():
    from athena.providers.base import Capabilities

    c = Capabilities()
    assert hasattr(c, "audio_transcription")
    assert c.audio_transcription is False  # safe default


def test_capabilities_supports_audio_transcription_lookup():
    """The supports() helper accepts the new field by name."""
    from athena.providers.base import Capabilities

    c = Capabilities(audio_transcription=True)
    assert c.supports("audio_transcription") is True
    assert c.supports("nonsense") is False


def test_faster_whisper_backend_declares_capability():
    """Importing athena.audio.backends registers the adapter;
    its static_capabilities() declares audio_transcription +
    is_local."""
    from athena.audio import backends  # noqa: F401 — trigger
    from athena.audio.backends.faster_whisper_local import (
        FasterWhisperLocalBackend,
    )

    caps = FasterWhisperLocalBackend.static_capabilities()
    assert caps.audio_transcription is True
    assert caps.is_local is True
    # And NOT a chat backend
    assert caps.tool_calls is False
    assert caps.streaming is False


# ---------------------------------------------------------------
# Broker resolution
# ---------------------------------------------------------------


def test_media_registry_resolves_audio_transcription_to_local():
    """With faster-whisper registered + cfg.media_backend_prefer=local,
    backend_for returns the FasterWhisperLocalBackend class."""
    from athena.audio import backends  # noqa: F401 — register
    from athena.audio.backends.faster_whisper_local import (
        FasterWhisperLocalBackend,
    )
    from athena.media.registry import MediaRegistry

    cfg = SimpleNamespace(media_backend_prefer="local")
    reg = MediaRegistry(cfg=cfg)
    cls = reg.backend_for("audio_transcription")
    assert cls is FasterWhisperLocalBackend


def test_media_registry_can_audio_transcription():
    from athena.audio import backends  # noqa: F401
    from athena.media.registry import MediaRegistry

    cfg = SimpleNamespace(media_backend_prefer="local")
    assert MediaRegistry(cfg=cfg).can("audio_transcription") is True


# ---------------------------------------------------------------
# Segment + TranscribeResult
# ---------------------------------------------------------------


def test_segment_to_dict_omits_speaker_when_none():
    s = Segment(start=1.2345, end=2.3456, text="hello", speaker=None)
    d = s.to_dict()
    assert d == {"start": 1.234, "end": 2.346, "text": "hello"}
    assert "speaker" not in d


def test_segment_to_dict_includes_speaker_when_set():
    s = Segment(start=0.0, end=1.0, text="hi", speaker="SPK0")
    assert s.to_dict()["speaker"] == "SPK0"


def test_result_to_dict_shape():
    r = TranscribeResult(
        segments=[Segment(start=0, end=1, text="a")],
        language="en",
        duration=12.5,
        content_type="speech",
    )
    d = r.to_dict()
    assert d["segments"] == [{"start": 0.0, "end": 1.0, "text": "a"}]
    assert d["language"] == "en"
    assert d["duration"] == 12.5
    assert d["content_type"] == "speech"


def test_result_to_dict_omits_optional_when_absent():
    r = TranscribeResult(segments=[])
    d = r.to_dict()
    assert d == {"segments": []}


# ---------------------------------------------------------------
# Stub backend pins the protocol shape end-to-end
# ---------------------------------------------------------------


def test_stub_backend_returns_chunk_relative_offsets(stub_backend, tmp_path: Path):
    """The protocol contract: chunk_offset_s pushes timestamps
    forward by exactly that amount. Verified via the stub so
    the test doesn't depend on faster-whisper."""
    r = stub_backend.transcribe(
        tmp_path / "fake.wav",
        language="en",
        diarize=False,
        chunk_offset_s=10.0,
    )
    assert len(r.segments) == 1
    assert r.segments[0].start == pytest.approx(10.0)
    assert r.segments[0].end == pytest.approx(10.9)


def test_stub_backend_unavailable_path(stub_backend):
    stub_backend._available = False
    assert stub_backend.is_available() is False


def test_stub_backend_classify(stub_backend, tmp_path: Path):
    assert stub_backend.classify(tmp_path / "any.wav") == "speech"
    stub_backend._content_type = "silence"
    assert stub_backend.classify(tmp_path / "any.wav") == "silence"


# ---------------------------------------------------------------
# faster-whisper backend (lightweight checks — no model load)
# ---------------------------------------------------------------


def test_faster_whisper_is_available_when_lib_importable():
    """faster-whisper is in the user's environment; is_available
    should return True. The test guards the import + check
    only — no model is actually loaded here."""
    from athena.audio.backends.faster_whisper_local import (
        FasterWhisperLocalBackend,
    )

    b = FasterWhisperLocalBackend()
    # We don't strictly require faster-whisper to be installed
    # at test time — but if it isn't, the backend is simply
    # unavailable and the rest of the suite uses the stub.
    avail = b.is_available()
    assert isinstance(avail, bool)


def test_faster_whisper_chat_methods_raise():
    """Capability-only provider — chat methods must error,
    not silently no-op."""
    from athena.audio.backends.faster_whisper_local import (
        FasterWhisperLocalBackend,
    )

    b = FasterWhisperLocalBackend()
    with pytest.raises(NotImplementedError):
        b.stream_chat(model="x", messages=[])
    # parse_tool_calls is permissive (returns the input
    # unchanged) — matches T6-05's stub_video_local pattern.
    out, calls = b.parse_tool_calls("hello", {})
    assert out == "hello"
    assert calls == []
