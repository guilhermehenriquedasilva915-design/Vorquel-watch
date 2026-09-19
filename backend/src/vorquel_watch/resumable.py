"""Checkpointed long-media transcription for FAST mode.

The worker processes fixed core windows with a small overlap on both sides.
Overlap is transcribed for context but only segments whose midpoint belongs to
the core window are persisted. That gives boundary context without duplicate
evidence.

Each chunk and its checkpoint are committed atomically by
persist_transcription_chunk(). If the process dies after any completed chunk,
the lease can be reclaimed and work resumes from next_core_start_ms instead of
starting the recording again.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from vorquel_watch.ids import stable_id
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.logging_utils import log_event
from vorquel_watch.transcription import FasterWhisperEngine, JobCancelled, LeaseLost


LOG = logging.getLogger("vorquel_watch.transcription")


@dataclass(frozen=True, slots=True)
class ChunkWindow:
    index: int
    core_start_ms: int
    core_end_ms: int
    clip_start_ms: int
    clip_end_ms: int


def chunk_windows(
    duration_ms: int,
    *,
    chunk_ms: int,
    overlap_ms: int,
) -> list[ChunkWindow]:
    if duration_ms <= 0:
        raise ValueError("duration must be positive")
    if chunk_ms < 30_000:
        raise ValueError("transcription chunk must be at least 30 seconds")
    if overlap_ms < 0 or overlap_ms * 2 >= chunk_ms:
        raise ValueError("transcription overlap is invalid")

    windows: list[ChunkWindow] = []
    core_start = 0
    index = 0
    while core_start < duration_ms:
        core_end = min(duration_ms, core_start + chunk_ms)
        windows.append(
            ChunkWindow(
                index=index,
                core_start_ms=core_start,
                core_end_ms=core_end,
                clip_start_ms=max(0, core_start - overlap_ms),
                clip_end_ms=min(duration_ms, core_end + overlap_ms),
            )
        )
        core_start = core_end
        index += 1
    return windows


def _clip_spec(window: ChunkWindow) -> str:
    return f"{window.clip_start_ms / 1000:.3f},{window.clip_end_ms / 1000:.3f}"


def _base_checkpoint(
    *,
    transcript_id: str,
    chunk_ms: int,
    overlap_ms: int,
    language: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "transcript_id": transcript_id,
        "chunk_ms": chunk_ms,
        "overlap_ms": overlap_ms,
        "chunk_index": 0,
        "next_core_start_ms": 0,
        "next_ordinal": 0,
        "word_count": 0,
        "text_bytes": 0,
        "language": language,
        "complete": False,
    }


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    *,
    transcript_id: str,
    chunk_ms: int,
    overlap_ms: int,
    duration_ms: int,
) -> None:
    if checkpoint.get("transcript_id") != transcript_id:
        raise RuntimeError("checkpoint transcript identity mismatch")
    if int(checkpoint.get("chunk_ms", -1)) != chunk_ms:
        raise RuntimeError("checkpoint chunk size mismatch")
    if int(checkpoint.get("overlap_ms", -1)) != overlap_ms:
        raise RuntimeError("checkpoint overlap mismatch")
    next_start = int(checkpoint.get("next_core_start_ms", -1))
    if next_start < 0 or next_start > duration_ms:
        raise RuntimeError("checkpoint position is invalid")
    if int(checkpoint.get("next_ordinal", -1)) < 0:
        raise RuntimeError("checkpoint ordinal is invalid")


def _transcript_payload(
    *,
    transcript_id: str,
    job: dict[str, Any],
    source: dict[str, Any],
    engine: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transcript_id": transcript_id,
        "schema_version": "1.0",
        "source_id": source["source_id"],
        "job_id": job["job_id"],
        "language": job.get("language_hint"),
        "text_source": "ASR",
        "segment_count": 0,
        "word_count": 0,
        "duration_ms": int(source["duration_ms"]),
        "alignment": "NONE",
        "diarization": "NONE",
        "engine": engine,
        "data_trust_class": "UNTRUSTED_DERIVED",
        "instruction_authority": "NONE",
    }


def _rows_for_window(
    segments_iter,
    *,
    window: ChunkWindow,
    transcript_id: str,
    source_id: str,
    job_id: str,
    ordinal_start: int,
    provenance: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    words = 0
    text_bytes = 0

    for local_index, segment in enumerate(segments_iter):
        text = (segment.text or "").strip()
        if not text:
            continue
        start_ms = max(0, int(float(segment.start) * 1000))
        end_ms = max(start_ms, int(float(segment.end) * 1000))

        # clip_timestamps on WhisperModel uses the source timeline. Refuse
        # obviously relative timestamps rather than silently corrupting evidence.
        if (
            window.clip_start_ms > 0
            and end_ms + 1000 < window.clip_start_ms
        ):
            raise RuntimeError("transcription engine returned non-global clip timestamps")

        midpoint = start_ms + (end_ms - start_ms) // 2
        is_last_core = window.core_end_ms == window.clip_end_ms
        in_core = (
            window.core_start_ms <= midpoint < window.core_end_ms
            or (is_last_core and midpoint == window.core_end_ms)
        )
        if not in_core:
            continue

        ordinal = ordinal_start + len(rows)
        segment_id = stable_id(
            "seg_",
            transcript_id,
            window.index,
            local_index,
            start_ms,
            end_ms,
        )
        rows.append(
            {
                "segment_id": segment_id,
                "schema_version": "1.0",
                "transcript_id": transcript_id,
                "source_id": source_id,
                "ordinal": ordinal,
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
                "provenance": {
                    **provenance,
                    "chunk_index": window.index,
                    "clip_start_ms": window.clip_start_ms,
                    "clip_end_ms": window.clip_end_ms,
                },
                "data_trust_class": "UNTRUSTED_DERIVED",
                "instruction_authority": "NONE",
            }
        )
        words += len(text.split())
        text_bytes += len(text.encode("utf-8"))

    return rows, words, text_bytes


def transcribe_resumable(
    repo,
    engine: FasterWhisperEngine,
    job: dict[str, Any],
    *,
    worker_id: str,
    lease_seconds: int,
) -> dict[str, Any]:
    """Transcribe a FAST job with durable per-chunk checkpoints."""
    if job["mode"] != "FAST":
        raise ValueError("only FAST mode is available in the alpha")

    source = repo.get_source(job["source_id"])
    if not source or source["ingest_status"] != "READY":
        raise ValueError("source is not ready")

    settings = engine.settings
    duration_ms = int(source["duration_ms"])
    windows = chunk_windows(
        duration_ms,
        chunk_ms=settings.transcription_chunk_ms,
        overlap_ms=settings.transcription_overlap_ms,
    )

    storage = LocalStorage(settings.data_dir)
    media_path: Path = storage.verify_object(source["content_sha256"])
    descriptor = engine.engine_descriptor()
    transcript_id = stable_id("trn_", job["job_id"])
    transcript = repo.ensure_transcript(
        _transcript_payload(
            transcript_id=transcript_id,
            job=job,
            source=source,
            engine=descriptor,
        )
    )

    initial = _base_checkpoint(
        transcript_id=transcript_id,
        chunk_ms=settings.transcription_chunk_ms,
        overlap_ms=settings.transcription_overlap_ms,
        language=job.get("language_hint"),
    )
    run = repo.ensure_processing_run(
        job_id=job["job_id"],
        stage="TRANSCRIBING",
        checkpoint=initial,
    )
    checkpoint = dict(run.get("checkpoint") or initial)
    _validate_checkpoint(
        checkpoint,
        transcript_id=transcript_id,
        chunk_ms=settings.transcription_chunk_ms,
        overlap_ms=settings.transcription_overlap_ms,
        duration_ms=duration_ms,
    )

    if run.get("status") == "SUCCEEDED" and checkpoint.get("complete"):
        return repo.get_transcript_for_job(job["job_id"]) or transcript

    model = engine._load_model()
    provenance = {
        "job_id": job["job_id"],
        "engine": descriptor["name"],
        "model": descriptor["model"],
        "model_repository": descriptor["model_repository"],
        "model_revision": descriptor["model_revision"],
        "engine_version": descriptor["version"],
        "runtime_version": descriptor["runtime_version"],
        "device_class": descriptor["device_class"],
    }

    start_at = int(checkpoint["next_core_start_ms"])
    pending = [window for window in windows if window.core_start_ms >= start_at]

    for window in pending:
        if not repo.heartbeat(job["job_id"], worker_id, lease_seconds):
            raise LeaseLost()

        last_error: Exception | None = None
        rows: list[dict[str, Any]] = []
        chunk_words = 0
        chunk_bytes = 0
        detected_language = checkpoint.get("language") or job.get("language_hint")

        for attempt in range(settings.transcription_chunk_retries + 1):
            try:
                segments_iter, info = model.transcribe(
                    str(media_path),
                    language=detected_language or None,
                    beam_size=5,
                    vad_filter=False,
                    word_timestamps=False,
                    clip_timestamps=_clip_spec(window),
                )
                rows, chunk_words, chunk_bytes = _rows_for_window(
                    segments_iter,
                    window=window,
                    transcript_id=transcript_id,
                    source_id=source["source_id"],
                    job_id=job["job_id"],
                    ordinal_start=int(checkpoint["next_ordinal"]),
                    provenance=provenance,
                )
                detected_language = detected_language or getattr(info, "language", None)
                last_error = None
                break
            except (JobCancelled, LeaseLost):
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= settings.transcription_chunk_retries:
                    break
                if not repo.heartbeat(job["job_id"], worker_id, lease_seconds):
                    raise LeaseLost()
                log_event(
                    LOG,
                    logging.WARNING,
                    "transcription_chunk_retry",
                    job_id=job["job_id"],
                    source_id=source["source_id"],
                    chunk_index=window.index,
                    attempt=attempt + 1,
                    exception_type=type(exc).__name__,
                )

        if last_error is not None:
            repo.finish_processing_run(
                run["run_id"],
                checkpoint=checkpoint,
                status="FAILED",
            )
            raise last_error

        next_segment_count = int(checkpoint["next_ordinal"]) + len(rows)
        next_word_count = int(checkpoint["word_count"]) + chunk_words
        next_text_bytes = int(checkpoint["text_bytes"]) + chunk_bytes

        if next_segment_count > settings.max_transcript_segments:
            raise ValueError("transcript exceeds configured segment limit")
        if next_text_bytes > settings.max_transcript_text_bytes:
            raise ValueError("transcript exceeds configured text limit")

        next_checkpoint = {
            **checkpoint,
            "chunk_index": window.index + 1,
            "next_core_start_ms": window.core_end_ms,
            "next_ordinal": next_segment_count,
            "word_count": next_word_count,
            "text_bytes": next_text_bytes,
            "language": detected_language,
        }
        repo.persist_transcription_chunk(
            job_id=job["job_id"],
            run_id=run["run_id"],
            transcript_id=transcript_id,
            segments=rows,
            checkpoint=next_checkpoint,
        )
        checkpoint = next_checkpoint

        progress = min(
            900,
            10 + int((window.core_end_ms / duration_ms) * 880),
        )
        repo.update_job(
            job["job_id"],
            {"stage": "TRANSCRIBING", "progress_permille": progress},
        )
        log_event(
            LOG,
            logging.INFO,
            "transcription_chunk_committed",
            job_id=job["job_id"],
            source_id=source["source_id"],
            chunk_index=window.index,
            core_end_ms=window.core_end_ms,
            segments=len(rows),
        )

    complete_checkpoint = {**checkpoint, "complete": True}
    repo.finish_transcript(
        transcript_id=transcript_id,
        segment_count=int(checkpoint["next_ordinal"]),
        word_count=int(checkpoint["word_count"]),
        language=checkpoint.get("language"),
    )
    repo.finish_processing_run(
        run["run_id"],
        checkpoint=complete_checkpoint,
        status="SUCCEEDED",
    )
    result = repo.get_transcript_for_job(job["job_id"]) or transcript
    return {
        **result,
        "segment_count": int(checkpoint["next_ordinal"]),
        "word_count": int(checkpoint["word_count"]),
        "language": checkpoint.get("language"),
    }
