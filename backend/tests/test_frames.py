"""Frame timeline, sampling, change detection and dedupe.

Fixtures are synthesized locally: a video of distinct "screens", each held for
a couple of seconds, which is the shape a workshop recording actually has.
"""

import unittest
from pathlib import Path
import tempfile

try:
    import av
    import numpy as np
except ImportError:  # pragma: no cover
    av = None
    np = None

from vorquel_watch.frames import (
    DEFAULT_CHANGE_THRESHOLD,
    change_score,
    fingerprint,
    frame_at,
    group_observations,
    read_timeline,
    sample_frames,
)


FPS = 10
HELD_FRAMES = 20
SCREENS = [(20, 30, 200), (200, 40, 30), (30, 180, 60)]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _write_screens(path: Path, screens=SCREENS, held=HELD_FRAMES) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=FPS)
    stream.width, stream.height = 320, 240
    stream.pix_fmt = "yuv420p"

    for index in range(len(screens) * held):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[:, :] = screens[index // held]
        frame[100:140, 40:280] = (255, 255, 255)
        for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class FrameTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.video = Path(self._tmp.name) / "screens.mp4"
        _write_screens(self.video)
        self.addCleanup(self._tmp.cleanup)

    def test_timeline_reports_real_timing(self) -> None:
        timeline = read_timeline(self.video)

        expected_ms = len(SCREENS) * HELD_FRAMES * 1000 // FPS
        self.assertAlmostEqual(timeline.duration_ms, expected_ms, delta=300)
        self.assertAlmostEqual(timeline.average_fps, FPS, delta=0.5)
        self.assertEqual((timeline.width, timeline.height), (320, 240))

    def test_timeline_requires_a_video_stream(self) -> None:
        audio_only = Path(self._tmp.name) / "noviz.wav"
        audio_only.write_bytes(b"not a video")
        with self.assertRaises(ValueError):
            read_timeline(audio_only)


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class ChangeDetectionTests(unittest.TestCase):
    def test_identical_fingerprints_score_zero(self) -> None:
        a = np.full((32, 32), 100, dtype=np.int16)
        self.assertEqual(change_score(a, a.copy()), 0.0)

    def test_no_previous_frame_is_maximum_change(self) -> None:
        a = np.full((32, 32), 100, dtype=np.int16)
        self.assertEqual(change_score(None, a), 1.0)

    def test_opposite_frames_score_near_one(self) -> None:
        black = np.zeros((32, 32), dtype=np.int16)
        white = np.full((32, 32), 255, dtype=np.int16)
        self.assertGreater(change_score(black, white), 0.9)

    def test_mismatched_shapes_are_treated_as_a_change(self) -> None:
        self.assertEqual(
            change_score(np.zeros((8, 8), dtype=np.int16), np.zeros((32, 32), dtype=np.int16)),
            1.0,
        )


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class SamplingAndDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.video = Path(self._tmp.name) / "screens.mp4"
        _write_screens(self.video)
        self.addCleanup(self._tmp.cleanup)
        self.timeline = read_timeline(self.video)

    def test_sampling_reads_far_fewer_frames_than_exist(self) -> None:
        """The point of sampling: a long recording must not cost every frame."""
        samples = list(sample_frames(self.video))
        self.assertGreater(len(samples), 0)
        self.assertLess(len(samples), len(SCREENS) * HELD_FRAMES)

    def test_screen_changes_are_detected(self) -> None:
        samples = list(sample_frames(self.video))
        changed = [s for s in samples if s.change_score >= DEFAULT_CHANGE_THRESHOLD]
        # Two transitions between three screens, plus the opening frame.
        self.assertGreaterEqual(len(changed), 2)

    def test_max_samples_is_respected(self) -> None:
        samples = list(sample_frames(self.video, max_samples=2))
        self.assertEqual(len(samples), 2)

    def test_a_held_screen_becomes_one_observation(self) -> None:
        """Forty seconds of one slide is one row, not forty."""
        samples = list(sample_frames(self.video))
        spans = group_observations(samples, duration_ms=self.timeline.duration_ms)

        self.assertGreaterEqual(len(spans), len(SCREENS))
        self.assertLess(len(spans), len(samples) + 1)
        for span in spans:
            self.assertLessEqual(span.start_ms, span.end_ms)
            self.assertGreaterEqual(span.representative_frame_ms, span.start_ms)
            self.assertLessEqual(span.representative_frame_ms, span.end_ms)
            self.assertRegex(span.content_hash, r"\A[0-9a-f]{64}\Z")

    def test_spans_do_not_overlap_and_cover_in_order(self) -> None:
        samples = list(sample_frames(self.video))
        spans = group_observations(samples, duration_ms=self.timeline.duration_ms)

        for earlier, later in zip(spans, spans[1:]):
            self.assertLessEqual(earlier.end_ms, later.start_ms + 1)

    def test_no_samples_produces_no_observations(self) -> None:
        self.assertEqual(group_observations([], duration_ms=1000), [])

    def test_invalid_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            list(sample_frames(self.video, interval_ms=0))


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class FrameRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.video = Path(self._tmp.name) / "screens.mp4"
        _write_screens(self.video)
        self.addCleanup(self._tmp.cleanup)

    def test_frame_at_returns_a_real_png(self) -> None:
        png, actual_ms = frame_at(self.video, 3000)

        self.assertTrue(png.startswith(PNG_MAGIC))
        self.assertGreater(len(png), 100)
        self.assertAlmostEqual(actual_ms, 3000, delta=500)

    def test_frame_at_reports_the_timestamp_it_actually_found(self) -> None:
        """The caller is told what it got, not what it asked for."""
        _, early = frame_at(self.video, 0)
        _, late = frame_at(self.video, 5000)
        self.assertLess(early, late)

    def test_negative_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            frame_at(self.video, -1)


if __name__ == "__main__":
    unittest.main()
