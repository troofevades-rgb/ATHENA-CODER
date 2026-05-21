"""Audio test fixtures + helpers (T4-04).

Synthesised WAV fixtures so the suite runs without checking
binary blobs into the repo — same reproducibility story as the
T4-01 vision fixtures and T4-02 video fixtures.

The real faster-whisper model load is heavy (~74 MB for the
"base" model), so tests use a stub backend at the AudioBackend
Protocol level. The faster-whisper adapter is exercised
end-to-end in the live smoke step (T4-04.3) rather than every
test run.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

import pytest

from athena.audio.job import ContentType, Segment, TranscribeResult


def make_wav(
    path: Path,
    *,
    duration_s: float = 1.0,
    rate: int = 16000,
    freq_hz: int = 440,
    amplitude: float = 0.25,
) -> Path:
    """Write a deterministic mono 16-bit PCM WAV at ``path``.
    A pure sine at ``freq_hz`` — not real speech, but the
    backend tests use stubs so the audio content doesn't
    matter; only the file structure + duration does."""
    n_frames = int(duration_s * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(rate)
        for i in range(n_frames):
            v = int(amplitude * 32767.0 * math.sin(2 * math.pi * freq_hz * i / rate))
            w.writeframes(struct.pack("<h", v))
    return path


# ---------------------------------------------------------------
# Stub backend — implements the AudioBackend protocol with
# deterministic output. Used to test the tool + chunking layer
# without loading any real ML model.
# ---------------------------------------------------------------


class StubAudioBackend:
    """Returns one segment per chunk_offset call, with text
    derived from the offset so the stitch tests can verify
    that segments from later chunks carry the right absolute
    timestamps.

    Configurable ``diarize_supported`` so one test can pin
    that ``speaker=None`` is the right answer when the
    backend doesn't support diarization but the caller asked.
    """

    name = "stub"

    def __init__(
        self,
        *,
        available: bool = True,
        per_chunk_segments: int = 1,
        diarize_supported: bool = False,
        content_type: ContentType = "speech",
        raise_on_transcribe: Exception | None = None,
    ):
        self._available = available
        self._per_chunk = per_chunk_segments
        self._diarize_supported = diarize_supported
        self._content_type = content_type
        self._raise = raise_on_transcribe
        self.transcribe_calls: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self._available

    def transcribe(
        self,
        path,
        *,
        language=None,
        diarize=False,
        chunk_offset_s=0.0,
    ) -> TranscribeResult:
        if self._raise is not None:
            raise self._raise
        self.transcribe_calls.append({
            "path": str(path),
            "language": language,
            "diarize": diarize,
            "chunk_offset_s": chunk_offset_s,
        })
        segs: list[Segment] = []
        for i in range(self._per_chunk):
            seg_start = chunk_offset_s + i * 1.0
            seg_end = seg_start + 0.9
            speaker = (
                f"SPK{(i % 2)}"
                if (diarize and self._diarize_supported) else None
            )
            segs.append(Segment(
                start=seg_start,
                end=seg_end,
                text=f"chunk@{chunk_offset_s:.1f}s seg{i}",
                speaker=speaker,
            ))
        return TranscribeResult(
            segments=segs,
            language=language or "en",
            duration=None,
            content_type=None,
        )

    def classify(self, path) -> ContentType:
        return self._content_type


@pytest.fixture
def stub_backend() -> StubAudioBackend:
    """Per-test fresh stub."""
    return StubAudioBackend()
