"""T4-02.3 — ffprobe inspection tests.

Live-ffprobe tests are gated on ``have_ffprobe()``. The
fingerprint-hedging tests run without ffprobe (they operate on
synthetic probe dicts).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from athena.video.probe import (
    FFprobeMissing,
    codec_summary,
    encoder_fingerprint,
    ffprobe_json,
    gop_structure,
)
from tests.video.fixtures import FIXTURES_DIR, have_ffmpeg, have_ffprobe


_NEED_FFPROBE = pytest.mark.skipif(
    not have_ffprobe() or not have_ffmpeg(),
    reason="ffprobe / ffmpeg not on PATH",
)


# ---------------------------------------------------------------
# ffprobe_json — failure modes
# ---------------------------------------------------------------


def test_ffprobe_missing_raises_when_not_on_path(tmp_path: Path):
    """Force shutil.which to return None → FFprobeMissing."""
    with patch("athena.video.probe.shutil.which", return_value=None):
        with pytest.raises(FFprobeMissing, match="not found on PATH"):
            ffprobe_json(tmp_path / "anywhere.mp4")


@_NEED_FFPROBE
def test_ffprobe_json_returns_streams_and_format():
    out = ffprobe_json(FIXTURES_DIR / "sample.mp4")
    assert "streams" in out
    assert "format" in out
    assert any(s.get("codec_type") == "video" for s in out["streams"])


# ---------------------------------------------------------------
# codec_summary
# ---------------------------------------------------------------


@_NEED_FFPROBE
def test_codec_summary_extracts_codec():
    probe = ffprobe_json(FIXTURES_DIR / "sample.mp4")
    summary = codec_summary(probe)
    # testsrc encoded with libx264 → codec_name='h264'
    assert summary["codec_name"] in ("h264", "hevc", "h265", "vp9")
    assert summary["width"] == 320
    assert summary["height"] == 240


def test_codec_summary_no_video_stream_handles_gracefully():
    """A probe of an audio-only file returns no video streams —
    summary should report the error, not raise."""
    probe = {"streams": [{"codec_type": "audio"}], "format": {}}
    out = codec_summary(probe)
    assert out["error"] == "no video stream"


def test_codec_summary_empty_probe():
    assert codec_summary({})["error"] == "no video stream"


# ---------------------------------------------------------------
# encoder_fingerprint — synthetic dicts (no ffprobe needed)
# ---------------------------------------------------------------


def test_encoder_fingerprint_flags_x264_in_format_tag():
    probe = {
        "format": {"tags": {"encoder": "Lavf60.16.100 x264 - core 164"}},
        "streams": [],
    }
    fp = encoder_fingerprint(probe)
    assert fp["software_encoder_likely"] is True
    assert "x264/x265" in fp["interpretation"]


def test_encoder_fingerprint_flags_x265():
    probe = {
        "format": {"tags": {"encoder": "x265 - core 3.5+1"}},
        "streams": [],
    }
    fp = encoder_fingerprint(probe)
    assert fp["software_encoder_likely"] is True


def test_encoder_fingerprint_finds_stream_level_tag():
    probe = {
        "format": {"tags": {}},
        "streams": [{
            "codec_type": "video",
            "tags": {"encoder": "x264 - core"},
        }],
    }
    fp = encoder_fingerprint(probe)
    assert fp["software_encoder_likely"] is True
    assert fp["stream_encoder_tags"] == ["x264 - core"]


def test_encoder_fingerprint_hedges_on_absence():
    probe = {"format": {"tags": {"major_brand": "mp42"}}, "streams": []}
    fp = encoder_fingerprint(probe)
    assert fp["software_encoder_likely"] is False
    # The interpretation must say "absence is NOT proof".
    assert "NOT proof" in fp["interpretation"]


def test_encoder_fingerprint_collects_handler_names():
    probe = {
        "format": {"tags": {}},
        "streams": [{
            "codec_type": "video",
            "tags": {"handler_name": "Mainconcept Video Media Handler"},
        }],
    }
    fp = encoder_fingerprint(probe)
    assert "Mainconcept Video Media Handler" in fp["handler_names"]


@_NEED_FFPROBE
def test_encoder_fingerprint_on_x264_fixture():
    """The libx264-encoded fixture should report the x264 tag."""
    probe = ffprobe_json(FIXTURES_DIR / "x264_encoded.mp4")
    fp = encoder_fingerprint(probe)
    assert fp["software_encoder_likely"] is True


# ---------------------------------------------------------------
# gop_structure
# ---------------------------------------------------------------


def test_gop_missing_ffprobe_raises():
    with patch("athena.video.probe.shutil.which", return_value=None):
        with pytest.raises(FFprobeMissing):
            gop_structure("/path/nope.mp4")


@_NEED_FFPROBE
def test_gop_counts_frame_types():
    out = gop_structure(FIXTURES_DIR / "sample.mp4", limit=60)
    assert "frame_types_sample" in out
    # 3-second fixture at 24fps → at least 24 frames per second.
    assert out["frame_types_count"] >= 24
    # At least one I-frame at the start.
    assert out["i_frame_count"] >= 1


@_NEED_FFPROBE
def test_gop_regular_gop_pin_for_synthetic_clip():
    """testsrc encoded with x264 has a regular GOP — keyframe
    intervals should be uniform or near-uniform."""
    out = gop_structure(FIXTURES_DIR / "x264_encoded.mp4", limit=120)
    intervals = out["keyframe_intervals"]
    if intervals:
        # At most two distinct interval lengths (the last
        # truncated bucket can introduce one extra).
        assert len(set(intervals)) <= 2
