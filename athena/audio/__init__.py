"""Audio analysis (T4-04).

`audio_analyze` — timestamped transcription + optional speaker
diarization + coarse content classification. Backend resolved
via the T5-05 broker over the `audio_transcription` capability;
local-preferred by default so recordings stay on the machine.

Closes the gap T4-02's video tool punted on: the video tool's
`analyze` mode can now call :func:`athena.audio.tools.transcribe_track`
to transcribe an extracted audio stream and align the segments
back to frame timestamps.

  :mod:`athena.audio.job`       segment + result dataclasses +
                                AudioBackend Protocol
  :mod:`athena.audio.backends.faster_whisper_local`
                                local Whisper backend (faster-
                                whisper); declares is_local=True
  :mod:`athena.audio.tools`     the audio_analyze @tool entry
                                point with chunking
"""

from __future__ import annotations
