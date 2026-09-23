"""Offline tests for Visual Review Core V1.

PyAV writes tiny synthetic videos through its bundled FFmpeg libraries, so the
suite needs neither the network nor a system ffmpeg executable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import av
    import numpy as np
except ImportError:  # pragma: no cover
    av = None
    np = None

from vorquel_watch.frames import read_timeline
from vorquel_watch.visual_review import (
    BUDGET_PROFILES,
    SelectionMode,
    SelectionReason,
    _Candidate,
    _deduplicate,
    extract_frames,
    frame_budget,
)


FPS = 10
WIDTH = 640
HEIGHT = 360
SCREENS = (
    (25, 35, 190),
    (190, 45, 35),
    (30, 170, 70),
    (170, 35, 160),
)


def _write_video(path: Path, screens=SCREENS, held_frames: int = 15) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=FPS)
    stream.width, stream.height = WIDTH, HEIGHT
    stream.pix_fmt = "yuv420p"
    stream.gop_size = 10

    for index in range(len(screens) * held_frames):
        screen = index // held_frames
        image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        image[:, :] = screens[screen]
        # High-contrast table/code-like content makes scene changes explicit.
        image[40:45, 40:600] = (255, 255, 255)
        image[80 + screen * 12 : 86 + screen * 12, 80:560] = (245, 245, 245)
        for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


@unittest.skipIf(av is None, "media runtime (av) is not installed")
class VisualReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.video = self.root / "visual-review.mp4"
        _write_video(self.video)
        self.timeline = read_timeline(self.video)
        self.addCleanup(self._tmp.cleanup)

    def extract(self, **kwargs):
        return extract_frames(
            self.video,
            source_id=kwargs.pop("source_id", "src_visual_a"),
            **kwargs,
        )

    def test_scene_aware_candidates_capture_screen_changes(self) -> None:
        result = self.extract(mode="balanced", dedup_threshold=0.0)

        reasons = {frame.selection_reason for frame in result.frames}
        self.assertIn(SelectionReason.SCENE_CHANGE, reasons)
        self.assertGreaterEqual(result.deduplicated_count, len(SCREENS))
        self.assertFalse(result.fallback_used)

    def test_efficient_mode_uses_keyframes(self) -> None:
        result = self.extract(mode=SelectionMode.EFFICIENT, dedup_threshold=0.0)

        self.assertTrue(
            any(frame.selection_reason is SelectionReason.KEYFRAME for frame in result.frames)
        )
        self.assertLessEqual(
            result.kept_count, BUDGET_PROFILES[SelectionMode.EFFICIENT].max_frames
        )

    def test_uniform_fallback_when_scene_detector_underproduces(self) -> None:
        static = self.root / "static.mp4"
        _write_video(static, screens=((40, 40, 40),), held_frames=50)

        result = extract_frames(static, source_id="src_static", mode="balanced")

        self.assertTrue(result.fallback_used)
        self.assertTrue(
            any(frame.selection_reason is SelectionReason.FALLBACK for frame in result.frames)
        )

    def test_perceptual_dedup_collapses_held_screen(self) -> None:
        static = self.root / "held.mp4"
        _write_video(static, screens=((50, 60, 70),), held_frames=80)

        result = extract_frames(static, source_id="src_held", mode="detailed")

        self.assertGreater(result.candidate_count, result.deduplicated_count)
        self.assertEqual(result.deduplicated_count, result.kept_count)
        # First/last are mandatory; one compression-drift sample may also be
        # distinct at the intentionally conservative threshold.
        self.assertLessEqual(result.kept_count, 3)

    def test_first_and_last_temporal_coverage_survive(self) -> None:
        result = self.extract(mode="detailed", max_frames=3, dedup_threshold=0.0)

        self.assertLessEqual(result.frames[0].timestamp_ms, 200)
        self.assertGreaterEqual(
            result.frames[-1].timestamp_ms, self.timeline.duration_ms - 300
        )

    def test_frame_cap_is_enforced_after_dedup(self) -> None:
        result = self.extract(mode="detailed", max_frames=3, dedup_threshold=0.0)

        self.assertEqual(result.kept_count, 3)
        self.assertLessEqual(result.kept_count, result.deduplicated_count)

    def test_pinned_timestamp_has_priority_and_reason(self) -> None:
        result = self.extract(
            mode="balanced",
            timestamps_ms=[3_100],
            max_frames=3,
            dedup_threshold=1.0,
        )

        cues = [
            frame
            for frame in result.frames
            if frame.selection_reason is SelectionReason.TRANSCRIPT_CUE
        ]
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].timestamp_ms, 3_100, delta=200)

    def test_short_video_budget_expands_for_boundaries_and_pinned_cue(self) -> None:
        result = self.extract(mode="balanced", timestamps_ms=[2_100])

        self.assertGreaterEqual(result.kept_count, 3)
        self.assertTrue(
            any(
                frame.selection_reason is SelectionReason.TRANSCRIPT_CUE
                for frame in result.frames
            )
        )

    def test_focused_range_only_returns_absolute_timestamps_in_window(self) -> None:
        result = self.extract(
            mode="detailed",
            start_ms=1_700,
            end_ms=4_200,
            dedup_threshold=0.0,
        )

        self.assertEqual((result.start_ms, result.end_ms), (1_700, 4_200))
        self.assertTrue(all(1_700 <= frame.timestamp_ms <= 4_200 for frame in result.frames))
        self.assertGreater(result.frames[0].timestamp_ms, 1_000)

    def test_focused_range_uses_denser_budget(self) -> None:
        normal = frame_budget(30_000, mode="balanced", focused=False)
        focused = frame_budget(30_000, mode="balanced", focused=True)

        self.assertGreater(focused.target_count, normal.target_count)
        self.assertLess(focused.interval_ms, normal.interval_ms)

    def test_resolution_preserves_aspect_ratio(self) -> None:
        result = self.extract(mode="efficient", resolution=320, max_frames=2)

        for frame in result.frames:
            self.assertEqual((frame.width, frame.height), (320, 180))
            self.assertTrue(frame.image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_provenance_and_trust_metadata_are_explicit(self) -> None:
        result = self.extract(mode="balanced", max_frames=2)

        for frame in result.frames:
            record = frame.as_dict()
            self.assertEqual(record["source_id"], "src_visual_a")
            self.assertTrue(str(record["frame_id"]).startswith("art_"))
            self.assertTrue(str(record["artifact_id"]).startswith("art_"))
            self.assertRegex(str(record["artifact_sha256"]), r"\A[0-9a-f]{64}\Z")
            self.assertIn(record["selection_reason"], {reason.value for reason in SelectionReason})
            self.assertEqual(record["data_trust_class"], "UNTRUSTED_DERIVED")
            self.assertEqual(record["instruction_authority"], "NONE")
            self.assertNotIn("path", record)
            self.assertNotIn("image_bytes", record)

    def test_hostile_visual_content_never_gains_authority(self) -> None:
        result = self.extract(mode="balanced", max_frames=2)

        self.assertTrue(result.frames)
        self.assertTrue(
            all(frame.instruction_authority == "NONE" for frame in result.frames)
        )

    def test_invalid_timestamps_and_ranges_are_rejected(self) -> None:
        invalid = (
            {"timestamps_ms": [-1]},
            {"timestamps_ms": [self.timeline.duration_ms]},
            {"timestamps_ms": [1.5]},
            {"start_ms": 2_000, "end_ms": 1_000},
            {"start_ms": -1, "end_ms": 1_000},
            {"max_frames": 1},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.extract(**kwargs)

    def test_detector_failure_falls_back_without_failing_request(self) -> None:
        with mock.patch(
            "vorquel_watch.visual_review._scene_candidates",
            side_effect=RuntimeError("detector unavailable"),
        ):
            result = self.extract(mode="balanced")

        self.assertTrue(result.fallback_used)
        self.assertGreater(result.kept_count, 0)
        self.assertTrue(
            all(
                frame.selection_reason is SelectionReason.FALLBACK
                for frame in result.frames
            )
        )

    def test_dedup_failure_is_fail_open(self) -> None:
        first = _Candidate(0, SelectionReason.UNIFORM, np.zeros((64, 64)))
        middle = _Candidate(100, SelectionReason.UNIFORM, np.ones((64, 64)))
        last = _Candidate(200, SelectionReason.UNIFORM, np.ones((64, 64)) * 2)
        with mock.patch(
            "vorquel_watch.visual_review.change_score",
            side_effect=RuntimeError("comparison failed"),
        ):
            kept = _deduplicate([first, middle, last], 0.5)

        self.assertEqual(kept, [first, middle, last])

    def test_source_ids_isolate_frame_and_artifact_ids(self) -> None:
        first = self.extract(source_id="src_visual_a", mode="efficient", max_frames=2)
        second = self.extract(source_id="src_visual_b", mode="efficient", max_frames=2)

        self.assertNotEqual(first.frames[0].frame_id, second.frames[0].frame_id)
        self.assertNotEqual(first.frames[0].artifact_id, second.frames[0].artifact_id)
        self.assertTrue(all(frame.source_id == "src_visual_a" for frame in first.frames))
        self.assertTrue(all(frame.source_id == "src_visual_b" for frame in second.frames))

    def test_only_selected_artifacts_are_written_to_optional_workspace(self) -> None:
        workspace = self.root / "selected"
        result = self.extract(mode="detailed", max_frames=3, output_dir=workspace)

        files = list(workspace.glob("*.png"))
        self.assertEqual(len(files), result.kept_count)
        self.assertEqual({file.stem for file in files}, {f.artifact_id for f in result.frames})


if __name__ == "__main__":
    unittest.main()
