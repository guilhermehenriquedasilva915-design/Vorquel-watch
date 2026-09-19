from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version as package_version
from typing import Any

from vorquel_watch.config import Settings, resolve_model_pin
from vorquel_watch.db import WatchRepository
from vorquel_watch.ids import new_id
from vorquel_watch.local_storage import LocalStorage


class JobCancelled(Exception):
    pass


def _package_version(name: str) -> str:
    try:
        return package_version(name)
    except Exception:
        return "unknown"


@dataclass(slots=True)
class FasterWhisperEngine:
    settings: Settings
    _model: Any = None

    def model_pin(self) -> tuple[str, str]:
        """Resolve the exact model repository and commit this engine will use."""
        return resolve_model_pin(
            self.settings.whisper_model,
            self.settings.whisper_model_revision,
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Transcription support is not installed. "
                "Install vorquel-watch[transcribe]."
            ) from exc

        storage = LocalStorage(self.settings.data_dir)
        storage.ensure()

        # SEC-05: load the repository at an exact commit. Passing the logical
        # name alone would let upstream replace the weights between runs.
        repository, revision = self.model_pin()
        self._model = WhisperModel(
            repository,
            revision=revision,
            device=self.settings.whisper_device,
            compute_type=self.settings.whisper_compute_type,
            download_root=str(storage.models),
        )
        return self._model

    def engine_descriptor(self) -> dict[str, Any]:
        """Everything needed to reproduce, or to distrust, a transcript."""
        repository, revision = self.model_pin()
        return {
            "name": "faster-whisper",
            "model": self.settings.whisper_model,
            "model_repository": repository,
            "model_revision": revision,
            "version": _package_version("faster-whisper"),
            "runtime": "ctranslate2",
            "runtime_version": _package_version("ctranslate2"),
            "device_class": self.settings.whisper_device.upper(),
            "compute_type": self.settings.whisper_compute_type,
        }

    def transcribe_job(
        self,
        repo: WatchRepository,
        job: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if job["mode"] != "FAST":
            raise ValueError("only FAST mode is available in the alpha")

        source = repo.get_source(job["source_id"])
        if not source or source["ingest_status"] != "READY":
            raise ValueError("source is not ready")

        storage = LocalStorage(self.settings.data_dir)
        # SEC-04: re-hash before processing. A content-addressed hit proves only
        # that a file sits at that name; an object that changed on disk after
        # ingest must never be transcribed as though it were the original
        # evidence. A mismatch quarantines the object and fails the job.
        media_path = storage.verify_object(source["content_sha256"])

        engine = self.engine_descriptor()
        model = self._load_model()
        segments_iter, info = model.transcribe(
            str(media_path),
            language=job.get("language_hint") or None,
            beam_size=5,
            vad_filter=True,
            word_timestamps=False,
        )

        transcript_id = new_id("trn_")
        rows: list[dict[str, Any]] = []
        word_count = 0
        duration_ms = int(source["duration_ms"])
        last_progress = 10

        segment_provenance = {
            "job_id": job["job_id"],
            "engine": engine["name"],
            "model": engine["model"],
            "model_repository": engine["model_repository"],
            "model_revision": engine["model_revision"],
            "engine_version": engine["version"],
            "runtime_version": engine["runtime_version"],
            "device_class": engine["device_class"],
        }

        for ordinal, segment in enumerate(segments_iter):
            if ordinal % 25 == 0 and repo.is_cancelled(job["job_id"]):
                raise JobCancelled()

            text = (segment.text or "").strip()
            if not text:
                continue

            start_ms = max(0, int(segment.start * 1000))
            end_ms = max(start_ms, int(segment.end * 1000))
            word_count += len(text.split())
            rows.append(
                {
                    "segment_id": new_id("seg_"),
                    "schema_version": "1.0",
                    "transcript_id": transcript_id,
                    "source_id": source["source_id"],
                    "ordinal": len(rows),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "raw_text": text,
                    "effective_text": text,
                    "speaker_id": None,
                    "confidence": None,
                    "text_origin": "ASR",
                    "review_status": "UNREVIEWED",
                    "revision": 1,
                    "words": None,
                    "provenance": dict(segment_provenance),
                    "data_trust_class": "UNTRUSTED_DERIVED",
                    "instruction_authority": "NONE",
                }
            )

            if duration_ms > 0:
                progress = min(900, 10 + int((end_ms / duration_ms) * 880))
                if progress >= last_progress + 50:
                    repo.update_job(
                        job["job_id"],
                        {
                            "stage": "TRANSCRIBING",
                            "progress_permille": progress,
                        },
                    )
                    last_progress = progress

        transcript = {
            "transcript_id": transcript_id,
            "schema_version": "1.0",
            "source_id": source["source_id"],
            "job_id": job["job_id"],
            "language": getattr(info, "language", None),
            "text_source": "ASR",
            "segment_count": len(rows),
            "word_count": word_count,
            "duration_ms": duration_ms,
            "alignment": "NONE",
            "diarization": "NONE",
            "engine": engine,
            "data_trust_class": "UNTRUSTED_DERIVED",
            "instruction_authority": "NONE",
        }
        return transcript, rows


def persist_transcription(
    repo: WatchRepository,
    transcript: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    repo.create_transcript(transcript)

    batch_size = 500
    for start in range(0, len(rows), batch_size):
        repo.insert_segments(rows[start : start + batch_size])

    repo.finish_transcript(
        transcript_id=transcript["transcript_id"],
        segment_count=transcript["segment_count"],
        word_count=transcript["word_count"],
    )
