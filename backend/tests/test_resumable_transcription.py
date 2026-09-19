"""Checkpointed long-media transcription.

No external model or database is used here. The fake repository models only the
durable checkpoint contract, which lets the tests prove resume boundaries,
overlap filtering, retry and deterministic segment identity.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from vorquel_watch.config import Settings
from vorquel_watch.resumable import (
    _rows_for_window,
    chunk_windows,
    transcribe_resumable,
)
from vorquel_watch.transcription import FasterWhisperEngine


def _settings(**overrides) -> Settings:
    values = dict(
        data_dir=Path("."),
        supabase_url="https://example.invalid",
        supabase_secret_key="fake",
        transcription_chunk_ms=600_000,
        transcription_overlap_ms=2_000,
        transcription_chunk_retries=1,
    )
    values.update(overrides)
    return Settings(**values)


class FakeRepo:
    def __init__(self, *, duration_ms=1_500_000, checkpoint=None):
        self.source = {
            "source_id": "src_demo",
            "ingest_status": "READY",
            "content_sha256": "a" * 64,
            "duration_ms": duration_ms,
        }
        self.transcript = None
        self.run = None
        if checkpoint is not None:
            self.transcript = {
                "transcript_id": checkpoint["transcript_id"],
                "job_id": "job_demo",
                "source_id": "src_demo",
            }
            self.run = {
                "run_id": "run_resume",
                "job_id": "job_demo",
                "stage": "TRANSCRIBING",
                "status": "RUNNING",
                "checkpoint": dict(checkpoint),
            }
        self.persisted = []
        self.progress = []
        self.heartbeats = 0

    def get_source(self, _source_id):
        return dict(self.source)

    def heartbeat(self, *_args):
        self.heartbeats += 1
        return True

    def ensure_transcript(self, payload):
        if self.transcript is None:
            self.transcript = dict(payload)
        return dict(self.transcript)

    def get_transcript_for_job(self, _job_id):
        return dict(self.transcript) if self.transcript else None

    def ensure_processing_run(self, *, job_id, stage, checkpoint):
        if self.run is None:
            self.run = {
                "run_id": "run_demo",
                "job_id": job_id,
                "stage": stage,
                "status": "RUNNING",
                "checkpoint": dict(checkpoint),
            }
        return dict(self.run)

    def persist_transcription_chunk(
        self, *, job_id, run_id, transcript_id, segments, checkpoint
    ):
        self.persisted.append([dict(row) for row in segments])
        self.run["checkpoint"] = dict(checkpoint)
        return len(segments)

    def update_job(self, _job_id, values):
        self.progress.append(dict(values))

    def finish_transcript(self, *, transcript_id, segment_count, word_count, language=None):
        self.transcript.update(
            {
                "segment_count": segment_count,
                "word_count": word_count,
                "language": language,
            }
        )

    def finish_processing_run(self, run_id, *, checkpoint, status="SUCCEEDED"):
        self.run["status"] = status
        self.run["checkpoint"] = dict(checkpoint)


class FakeModel:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def transcribe(self, _path, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("synthetic transient failure")
        start_s, end_s = [float(v) for v in kwargs["clip_timestamps"].split(",")]
        # Three seconds after the clip begins is inside the core because the
        # configured overlap is two seconds.
        seg_start = min(end_s - 1.0, start_s + 3.0)
        segment = SimpleNamespace(text=f"chunk at {seg_start:.0f}", start=seg_start, end=seg_start + 0.8)
        return iter([segment]), SimpleNamespace(language="pt")


class ChunkPlanTests(unittest.TestCase):
    def test_core_windows_cover_duration_once_with_context_overlap(self):
        windows = chunk_windows(1_500_000, chunk_ms=600_000, overlap_ms=2_000)
        self.assertEqual(
            [(w.core_start_ms, w.core_end_ms) for w in windows],
            [(0, 600_000), (600_000, 1_200_000), (1_200_000, 1_500_000)],
        )
        self.assertEqual(windows[1].clip_start_ms, 598_000)
        self.assertEqual(windows[1].clip_end_ms, 1_202_000)

    def test_overlap_segments_are_not_persisted_twice(self):
        window = chunk_windows(1_200_000, chunk_ms=600_000, overlap_ms=2_000)[1]
        segments = iter(
            [
                SimpleNamespace(text="left overlap", start=598.2, end=598.8),
                SimpleNamespace(text="core", start=601.0, end=602.0),
            ]
        )
        rows, words, _ = _rows_for_window(
            segments,
            window=window,
            transcript_id="trn_demo",
            source_id="src_demo",
            job_id="job_demo",
            ordinal_start=7,
            provenance={"job_id": "job_demo"},
        )
        self.assertEqual([row["raw_text"] for row in rows], ["core"])
        self.assertEqual(rows[0]["ordinal"], 7)
        self.assertGreater(words, 0)


class ResumeTests(unittest.TestCase):
    def _run(self, repo, model):
        engine = FasterWhisperEngine(_settings())
        job = {
            "job_id": "job_demo",
            "source_id": "src_demo",
            "mode": "FAST",
            "language_hint": None,
        }
        with (
            mock.patch(
                "vorquel_watch.transcription.FasterWhisperEngine._load_model",
                return_value=model,
            ),
            mock.patch(
                "vorquel_watch.transcription.FasterWhisperEngine.engine_descriptor",
                return_value={
                    "name": "faster-whisper",
                    "model": "small",
                    "model_repository": "Systran/faster-whisper-small",
                    "model_revision": "a" * 40,
                    "version": "1.2.1",
                    "runtime": "ctranslate2",
                    "runtime_version": "4.6.0",
                    "device_class": "CPU",
                    "compute_type": "int8",
                },
            ),
            mock.patch(
                "vorquel_watch.resumable.LocalStorage.verify_object",
                return_value=Path("media.mp4"),
            ),
        ):
            return transcribe_resumable(
                repo, engine, job, worker_id="wkr_demo", lease_seconds=120
            )

    def test_fresh_job_commits_each_chunk_and_marks_run_complete(self):
        repo = FakeRepo()
        model = FakeModel()
        result = self._run(repo, model)

        self.assertEqual(len(model.calls), 3)
        self.assertEqual(len(repo.persisted), 3)
        self.assertTrue(repo.run["checkpoint"]["complete"])
        self.assertEqual(repo.run["checkpoint"]["next_core_start_ms"], 1_500_000)
        self.assertEqual(result["segment_count"], 3)

    def test_reclaimed_job_resumes_after_last_committed_core(self):
        transcript_id = "trn_" + "a" * 32
        checkpoint = {
            "schema_version": "1.0",
            "transcript_id": transcript_id,
            "chunk_ms": 600_000,
            "overlap_ms": 2_000,
            "chunk_index": 1,
            "next_core_start_ms": 600_000,
            "next_ordinal": 1,
            "word_count": 2,
            "text_bytes": 8,
            "language": "pt",
            "complete": False,
        }
        repo = FakeRepo(checkpoint=checkpoint)

        # stable transcript identity normally derives from job_id; align the
        # checkpoint to what the production helper will derive.
        from vorquel_watch.ids import stable_id
        expected = stable_id("trn_", "job_demo")
        repo.transcript["transcript_id"] = expected
        repo.run["checkpoint"]["transcript_id"] = expected

        model = FakeModel()
        result = self._run(repo, model)

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(repo.persisted), 2)
        self.assertEqual(result["segment_count"], 3)

    def test_transient_chunk_failure_retries_without_advancing_checkpoint(self):
        repo = FakeRepo(duration_ms=300_000)
        model = FakeModel(fail_first=True)
        result = self._run(repo, model)

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(repo.persisted), 1)
        self.assertTrue(repo.run["checkpoint"]["complete"])
        self.assertEqual(result["segment_count"], 1)

    def test_segment_identity_is_stable_for_same_chunk_output(self):
        repo1, repo2 = FakeRepo(duration_ms=300_000), FakeRepo(duration_ms=300_000)
        model1, model2 = FakeModel(), FakeModel()
        self._run(repo1, model1)
        self._run(repo2, model2)
        self.assertEqual(
            repo1.persisted[0][0]["segment_id"],
            repo2.persisted[0][0]["segment_id"],
        )


if __name__ == "__main__":
    unittest.main()
