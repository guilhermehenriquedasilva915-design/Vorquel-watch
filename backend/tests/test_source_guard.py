"""Source Guard probe/validation tests.

These cover the media-facing boundary: duration arithmetic, the container
allowlist and rejection of malformed input. Fixtures are synthesized locally so
no third-party media is downloaded and no personal data is involved.
"""

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

try:
    import av
except ImportError:  # pragma: no cover - core CI installs no media runtime
    av = None

from vorquel_watch.source_guard import probe_media, validate_and_hash


LIMITS = {
    "max_source_bytes": 25 * 1024 * 1024 * 1024,
    "max_duration_ms": 8 * 60 * 60 * 1000,
}

WAV_SECONDS = 3
WAV_RATE = 16000


def _write_wav(path: Path, seconds: int = WAV_SECONDS, rate: int = WAV_RATE) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * t / rate)))
                for t in range(rate * seconds)
            )
        )


def _write_container(
    path: Path,
    fmt: str,
    video_codec: str | None,
    audio_codec: str,
) -> None:
    """Mux a tiny synthetic clip so container policy can be exercised.

    video_codec is optional so audio-only containers can be produced too.
    """
    import numpy as np

    container = av.open(str(path), mode="w", format=fmt)
    audio_stream = container.add_stream(audio_codec, rate=WAV_RATE)

    if video_codec is not None:
        video_stream = container.add_stream(video_codec, rate=10)
        video_stream.width, video_stream.height = 160, 120
        video_stream.pix_fmt = "yuv420p"

        for index in range(20):
            frame = av.VideoFrame.from_ndarray(
                np.full((120, 160, 3), (index * 7) % 255, dtype=np.uint8),
                format="rgb24",
            )
            for packet in video_stream.encode(frame):
                container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)

    audio_frame = av.AudioFrame.from_ndarray(
        np.zeros((1, 1024), dtype=np.int16), format="s16", layout="mono"
    )
    audio_frame.rate = WAV_RATE
    for _ in range(32):
        try:
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)
        except Exception:
            break
    try:
        for packet in audio_stream.encode():
            container.mux(packet)
    except Exception:
        pass
    container.close()


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class SourceGuardProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_duration_is_not_inflated(self) -> None:
        """Regression: duration was multiplied by av.time_base instead of divided,
        inflating a 3s file to ~833 million hours and rejecting all valid media."""
        wav = self.root / "tone.wav"
        _write_wav(wav)

        probe = probe_media(wav)
        expected_ms = WAV_SECONDS * 1000
        self.assertAlmostEqual(probe.duration_ms, expected_ms, delta=150)
        self.assertLess(probe.duration_ms, LIMITS["max_duration_ms"])

    def test_valid_audio_is_accepted_end_to_end(self) -> None:
        wav = self.root / "tone.wav"
        _write_wav(wav)

        digest, byte_size, probe = validate_and_hash(wav, **LIMITS)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(byte_size, wav.stat().st_size)
        self.assertEqual(probe.source_kind, "LOCAL_AUDIO")
        self.assertEqual(probe.container, "wav")
        self.assertFalse(probe.has_video)
        self.assertTrue(probe.has_audio)

    def test_allowed_video_container_is_accepted(self) -> None:
        mp4 = self.root / "clip.mp4"
        _write_container(mp4, "mp4", "mpeg4", "aac")

        probe = probe_media(mp4)
        self.assertEqual(probe.source_kind, "LOCAL_VIDEO")
        self.assertEqual(probe.detected_mime, "video/mp4")
        self.assertGreater(probe.duration_ms, 0)
        self.assertLess(probe.duration_ms, LIMITS["max_duration_ms"])

    def test_every_allowlisted_container_round_trips(self) -> None:
        """Each allowlisted container must actually survive probe_media.

        Regression guard for allowlist drift: PyAV reports decoder
        implementation names, not codec names, so MP3 arrives as 'mp3float'.
        Listing only 'mp3' silently rejected every MP3 file while the container
        itself was allowlisted. Encoder names here are the write side; the
        assertion exercises the read side, which is what the guard inspects.
        """
        cases = [
            ("wav", "rt.wav", None, "pcm_s16le", "LOCAL_AUDIO", "audio/wav"),
            ("mp3", "rt.mp3", None, "mp3", "LOCAL_AUDIO", "audio/mpeg"),
            ("flac", "rt.flac", None, "flac", "LOCAL_AUDIO", "audio/flac"),
            ("ogg", "rt.ogg", None, "libopus", "LOCAL_AUDIO", "audio/ogg"),
            ("mp4", "rt.mp4", "mpeg4", "aac", "LOCAL_VIDEO", "video/mp4"),
            ("matroska", "rt.mkv", "mpeg4", "aac", "LOCAL_VIDEO", "video/webm"),
        ]

        for fmt, name, video_codec, audio_codec, kind, mime in cases:
            with self.subTest(container=fmt, audio_codec=audio_codec):
                path = self.root / name
                _write_container(path, fmt, video_codec, audio_codec)

                probe = probe_media(path)
                self.assertEqual(probe.source_kind, kind)
                self.assertEqual(probe.detected_mime, mime)
                self.assertTrue(probe.has_audio)
                self.assertGreater(probe.duration_ms, 0)
                self.assertLess(probe.duration_ms, LIMITS["max_duration_ms"])

    def test_avi_container_is_blocked(self) -> None:
        """AVI stays blocked: it reaches the CVE-2026-8461 demuxer class."""
        avi = self.root / "clip.avi"
        _write_container(avi, "avi", "mpeg4", "mp2")

        with self.assertRaises(ValueError) as ctx:
            probe_media(avi)
        self.assertIn("container format is not allowed", str(ctx.exception))

    def _assert_no_host_path(self, message: str, path: Path) -> None:
        """Rejection messages must never carry a host filesystem path."""
        self.assertNotIn(str(path), message)
        self.assertNotIn(path.name, message)
        self.assertNotIn(str(self.root), message)

    def test_unknown_container_is_rejected(self) -> None:
        bogus = self.root / "payload.mp4"
        bogus.write_bytes(b"this is not media at all" * 100)

        with self.assertRaises(ValueError) as ctx:
            probe_media(bogus)
        self._assert_no_host_path(str(ctx.exception), bogus)

    def test_truncated_media_is_rejected(self) -> None:
        mp4 = self.root / "clip.mp4"
        _write_container(mp4, "mp4", "mpeg4", "aac")
        truncated = self.root / "truncated.mp4"
        truncated.write_bytes(mp4.read_bytes()[: mp4.stat().st_size // 3])

        with self.assertRaises(ValueError) as ctx:
            probe_media(truncated)
        self._assert_no_host_path(str(ctx.exception), truncated)

    def test_rejection_cause_chain_carries_no_host_path(self) -> None:
        """A chained FFmpeg cause would leak the path into any logged traceback."""
        bogus = self.root / "payload.mp4"
        bogus.write_bytes(b"this is not media at all" * 100)

        with self.assertRaises(ValueError) as ctx:
            probe_media(bogus)
        self.assertIsNone(ctx.exception.__cause__)

    def test_empty_file_is_rejected(self) -> None:
        empty = self.root / "empty.wav"
        empty.write_bytes(b"")

        with self.assertRaises(ValueError):
            validate_and_hash(empty, **LIMITS)

    def test_oversized_file_is_rejected_before_probe(self) -> None:
        wav = self.root / "tone.wav"
        _write_wav(wav)

        limits = dict(LIMITS, max_source_bytes=16)
        with self.assertRaises(ValueError) as ctx:
            validate_and_hash(wav, **limits)
        self.assertIn("byte limit", str(ctx.exception))

    def test_duration_limit_still_enforced(self) -> None:
        wav = self.root / "tone.wav"
        _write_wav(wav)

        limits = dict(LIMITS, max_duration_ms=500)
        with self.assertRaises(ValueError) as ctx:
            validate_and_hash(wav, **limits)
        self.assertIn("duration limit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
