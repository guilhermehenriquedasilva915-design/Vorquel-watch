"""The screen pass inside a job.

One ingest should produce both tracks on one timeline, without the screen pass
becoming a way to lose a transcript that already succeeded.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vorquel_watch.local_storage import IntegrityError
from vorquel_watch.ocr import OcrUnavailable
from vorquel_watch.screen_pipeline import ScreenCancelled
from vorquel_watch.transcription import JobCancelled
from vorquel_watch.worker import _run_screen_pass


JOB = {"job_id": "job_abc", "source_id": "src_abc"}
VIDEO_SOURCE = {
    "source_id": "src_abc",
    "has_video": True,
    "content_sha256": "a" * 64,
}
AUDIO_SOURCE = {
    "source_id": "src_abc",
    "has_video": False,
    "content_sha256": "b" * 64,
}


def _settings(**overrides):
    base = {
        "data_dir": Path("."),
        "screen_enabled": True,
        "screen_interval_ms": 1500,
        "screen_change_threshold": 0.08,
        "screen_max_observations": 4000,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _screen_result(observations=1, blocks=2):
    return SimpleNamespace(
        observations=[{"observation_id": f"obs_{i}"} for i in range(observations)],
        text_blocks=[{"ocr_id": f"ocr_{i}"} for i in range(blocks)],
        frames_sampled=9,
        observations_kept=observations,
        ocr_runs=observations,
        ocr_reused=0,
        duration_ms=6000,
    )


class ScreenPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = mock.Mock()
        self.repo.get_source.return_value = VIDEO_SOURCE
        self.repo.heartbeat.return_value = True

        storage = mock.patch("vorquel_watch.local_storage.LocalStorage")
        self.storage = storage.start()
        self.storage.return_value.verify_object.return_value = Path("media.mp4")
        self.addCleanup(storage.stop)

    def _run(self, settings=None):
        return _run_screen_pass(
            self.repo, settings or _settings(), JOB, "wkr_test", 120
        )

    def test_audio_only_source_never_pays_for_the_screen_pass(self) -> None:
        self.repo.get_source.return_value = AUDIO_SOURCE

        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations"
        ) as build:
            self._run()

        build.assert_not_called()
        self.repo.insert_screen_observations.assert_not_called()

    def test_disabled_setting_skips_before_touching_the_database(self) -> None:
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations"
        ) as build:
            self._run(_settings(screen_enabled=False))

        build.assert_not_called()
        self.repo.get_source.assert_not_called()

    def test_observations_and_text_blocks_are_persisted(self) -> None:
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            return_value=_screen_result(observations=3, blocks=5),
        ):
            self._run()

        self.repo.insert_screen_observations.assert_called_once()
        self.assertEqual(
            len(self.repo.insert_screen_observations.call_args[0][0]), 3
        )
        self.repo.insert_screen_text_blocks.assert_called_once()

    def test_large_block_sets_are_inserted_in_batches(self) -> None:
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            return_value=_screen_result(observations=1, blocks=1200),
        ):
            self._run()

        self.assertEqual(self.repo.insert_screen_text_blocks.call_count, 3)

    def test_missing_screen_extra_keeps_the_transcript(self) -> None:
        """A transcript is a real result; an optional extra must not discard it."""
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            side_effect=OcrUnavailable("not installed"),
        ):
            self._run()

        self.repo.insert_screen_observations.assert_not_called()

    def test_losing_the_job_mid_pass_cancels_the_job(self) -> None:
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            side_effect=ScreenCancelled(),
        ):
            with self.assertRaises(JobCancelled):
                self._run()

        self.repo.insert_screen_observations.assert_not_called()

    def test_a_screen_failure_never_discards_a_finished_transcript(self) -> None:
        """Regression: a fault here used to fail the whole job.

        The transcript is the primary product and has already succeeded by this
        point. Failing the job over a fault in an enhancement would throw away
        hours of completed transcription, so the pass degrades and the failure
        is logged rather than raised.
        """
        for failure in (MemoryError("out of memory"), RuntimeError("decode blew up")):
            with self.subTest(failure=type(failure).__name__):
                self.repo.reset_mock()
                with mock.patch(
                    "vorquel_watch.screen_pipeline.build_screen_observations",
                    side_effect=failure,
                ):
                    self._run()

                self.repo.insert_screen_observations.assert_not_called()

    def test_a_persistence_failure_also_degrades(self) -> None:
        """The same reasoning covers a failure while writing the rows."""
        self.repo.insert_screen_observations.side_effect = RuntimeError("db down")

        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            return_value=_screen_result(),
        ):
            self._run()

    def test_a_corrupted_media_object_does_not_cost_the_transcript(self) -> None:
        """Regression: media verification sat outside the guard.

        verify_object raises when the stored object no longer matches its
        digest. That has to degrade like every other screen failure, or a
        quarantined object would fail a job whose transcription already
        finished.
        """
        self.storage.return_value.verify_object.side_effect = IntegrityError(
            "local media object failed integrity check"
        )

        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations"
        ) as build:
            self._run()

        build.assert_not_called()
        self.repo.insert_screen_observations.assert_not_called()

    def test_a_stage_update_failure_does_not_cost_the_transcript(self) -> None:
        self.repo.update_job.side_effect = RuntimeError("db down")

        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations"
        ) as build:
            self._run()

        build.assert_not_called()

    def test_the_pass_reports_progress_before_running(self) -> None:
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            return_value=_screen_result(),
        ):
            self._run()

        self.repo.update_job.assert_called_once()
        stage = self.repo.update_job.call_args[0][1]
        self.assertEqual(stage["stage"], "MERGING")

    def test_media_is_verified_before_it_is_read(self) -> None:
        """SEC-04 applies to the screen pass too."""
        with mock.patch(
            "vorquel_watch.screen_pipeline.build_screen_observations",
            return_value=_screen_result(),
        ):
            self._run()

        self.storage.return_value.verify_object.assert_called_once_with("a" * 64)


if __name__ == "__main__":
    unittest.main()
