"""Audio backend contract — the shape every STT adapter implements (T4-04.1).

Normalized output across backends. The audio_analyze tool maps
any backend's segments into this shape so the model and downstream
consumers see one consistent surface regardless of which engine
ran the transcription.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Literal, Protocol

ContentType = Literal["speech", "music", "silence", "mixed", "unknown"]


@dataclasses.dataclass(frozen=True)
class Segment:
    """One timestamped chunk of transcript.

    `start` / `end` are absolute seconds from the file start
    (not chunk-relative — the tool layer fixes up chunked
    results before returning).

    `speaker` is None when diarization was off / the backend
    doesn't support it / the speaker is unknown.
    """

    start: float
    end: float
    text: str
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "text": self.text,
        }
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d


@dataclasses.dataclass
class TranscribeResult:
    """Full result of a transcription call.

    `segments` — the normalized list. `language` is the detected
    language (ISO-639-1) when the backend reports one. `duration`
    is the source file's duration in seconds, when known.
    `content_type` is set only when the caller asked for it
    (`mode=classify`/`full`).
    """

    segments: list[Segment]
    language: str | None = None
    duration: float | None = None
    content_type: ContentType | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "segments": [s.to_dict() for s in self.segments],
        }
        if self.language is not None:
            d["language"] = self.language
        if self.duration is not None:
            d["duration"] = round(float(self.duration), 3)
        if self.content_type is not None:
            d["content_type"] = self.content_type
        return d


class AudioBackend(Protocol):
    """The transcription surface every adapter satisfies.

    Backends should:

      - declare ``audio_transcription=True`` in
        ``static_capabilities()`` so the broker picks them up
      - prefer ``is_local=True`` for on-device engines (whisper-
        class)
      - return all timestamps in absolute file-seconds
      - never raise into the tool layer for an unknown
        language / format / unsupported feature — return an
        empty `segments` list + log at WARNING

    Implementations live under ``athena/audio/backends/``;
    vendor specifics (model loading / API auth / network
    transport) stay isolated to that one file.
    """

    def is_available(self) -> bool:
        """Quick "this backend can run on this host" check. The
        tool consults this before routing; an unavailable
        backend lets the tool fall through to the next
        candidate or surface "no backend configured" cleanly.
        """
        ...

    def transcribe(
        self,
        path: Path | str,
        *,
        language: str | None = None,
        diarize: bool = False,
        chunk_offset_s: float = 0.0,
    ) -> TranscribeResult:
        """Transcribe one audio file.

        ``chunk_offset_s`` lets the tool layer pass a per-chunk
        offset when stitching long files — segment timestamps
        come back in absolute file-seconds, not chunk-relative.

        ``diarize`` is advisory: backends that don't support
        speaker labels return segments with ``speaker=None``
        (no error).
        """
        ...

    def classify(self, path: Path | str) -> ContentType:
        """Coarse content type. Backends without classifier
        support return ``"unknown"`` rather than raising."""
        ...
