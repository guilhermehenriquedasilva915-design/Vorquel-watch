"""Resource exhaustion limits (SEC-09).

These tests stay deterministic: they feed policy-shaped metadata or fake model
segments rather than allocating oversized real media.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from vorquel_watch.config import Settings
from vorquel_watch.source_guard import probe_media
from vorquel_watch.transcription import FasterWhisperEngine


def _settings(**overrides):
    values = dict(
        data_dir=Path("."),
        supabase_url="https://example.invalid",
        supabase_secret_key="not-a-real-key",
    )
    values.update(overrides)
    return Settings(**values)


class MediaPolicyLimitTests(TestCase):
    def _payload(self, *, width=1920, height=1080, rate=48000, channels=2):
        return {
            "ok": True,
            "format": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration_raw": 2_000_000,
            "time_base": 1_000_000,
            "streams": [
                {
                    "type": "video",
                    "codec": "h264",
                    "width": width,
                    "height": height,
                    "sample_rate": 0,
                    "channels": 0,
                },
                {
                    "type": "audio",
                    "codec": "aac",
                    "width": 0,
                    "height": 0,
                    "sample_rate": rate,
                    "channels": channels,
                },
            ],
        }

    def test_resolution_over_limit_is_rejected(self):
        with mock.patch(
            "vorquel_watch.source_guard._run_probe",
            return_value=self._payload(width=7680, height=4320),
        ):
            with self.assertRaisesRegex(ValueError, "resolution"):
                probe_media(Path("ignored"), max_video_width=3840, max_video_height=2160)

    def test_audio_rate_over_limit_is_rejected(self):
        with mock.patch(
            "vorquel_watch.source_guard._run_probe",
            return_value=self._payload(rate=384000),
        ):
            with self.assertRaisesRegex(ValueError, "sample rate"):
                probe_media(Path("ignored"), max_audio_sample_rate=192000)

    def test_audio_channels_over_limit_is_rejected(self):
        with mock.patch(
            "vorquel_watch.source_guard._run_probe",
            return_value=self._payload(channels=16),
        ):
            with self.assertRaisesRegex(ValueError, "channel count"):
                probe_media(Path("ignored"), max_audio_channels=8)

    def test_ordinary_workshop_metadata_passes(self):
        with mock.patch(
            "vorquel_watch.source_guard._run_probe",
            return_value=self._payload(),
        ):
            result = probe_media(Path("ignored"))
        self.assertEqual(result.max_video_width, 1920)
        self.assertEqual(result.max_video_height, 1080)
        self.assertEqual(result.max_audio_sample_rate, 48000)
        self.assertEqual(result.max_audio_channels, 2)


class TranscriptLimitTests(TestCase):
    def test_segment_limit_fails_closed(self):
        settings = _settings(max_transcript_segments=2)
        engine = FasterWhisperEngine(settings)

        repo = mock.Mock()
        repo.get_source.return_value = {
            "source_id": "src_demo",
            "ingest_status": "READY",
            "content_sha256": "a" * 64,
            "duration_ms": 10000,
        }
        repo.is_cancelled.return_value = False

        fake_model = mock.Mock()
        fake_model.transcribe.return_value = (
            iter(
                [
                    SimpleNamespace(text="one", start=0.0, end=1.0),
                    SimpleNamespace(text="two", start=1.0, end=2.0),
                    SimpleNamespace(text="three", start=2.0, end=3.0),
                ]
            ),
            SimpleNamespace(language="en"),
        )

        with (
            mock.patch.object(engine, "_load_model", return_value=fake_model),
            mock.patch(
                "vorquel_watch.transcription.LocalStorage.verify_object",
                return_value=Path("media.mp4"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "segment limit"):
                engine.transcribe_job(
                    repo,
                    {"job_id": "job_demo", "source_id": "src_demo", "mode": "FAST"},
                )

    def test_text_byte_limit_fails_closed(self):
        settings = _settings(
            max_transcript_segments=10,
            max_transcript_text_bytes=5,
        )
        engine = FasterWhisperEngine(settings)
        repo = mock.Mock()
        repo.get_source.return_value = {
            "source_id": "src_demo",
            "ingest_status": "READY",
            "content_sha256": "a" * 64,
            "duration_ms": 10000,
        }
        repo.is_cancelled.return_value = False
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = (
            iter([SimpleNamespace(text="123456", start=0.0, end=1.0)]),
            SimpleNamespace(language="en"),
        )
        with (
            mock.patch.object(engine, "_load_model", return_value=fake_model),
            mock.patch(
                "vorquel_watch.transcription.LocalStorage.verify_object",
                return_value=Path("media.mp4"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "text limit"):
                engine.transcribe_job(
                    repo,
                    {"job_id": "job_demo", "source_id": "src_demo", "mode": "FAST"},
                )


if __name__ == "__main__":
    import unittest
    unittest.main()
