from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from vorquel_watch.config import Settings
from vorquel_watch.db import DEFAULT_LEASE_SECONDS, WatchRepository
from vorquel_watch.logging_utils import configure_logging, log_event
from vorquel_watch.transcription import (
    FasterWhisperEngine,
    JobCancelled,
    persist_transcription,
)


LOG = logging.getLogger("vorquel_watch.worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_worker_id() -> str:
    """An opaque per-process identity.

    Deliberately not the hostname or the pid: worker ids end up in the database
    and in logs, and neither needs to carry anything about the machine.
    """
    return f"wkr_{uuid4().hex}"


def process_one(
    repo: WatchRepository,
    engine: FasterWhisperEngine,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    claimed = repo.claim_next_job(worker_id, lease_seconds)
    if not claimed:
        return False

    job_id = claimed["job_id"]
    source_id = claimed["source_id"]
    try:
        transcript, rows = engine.transcribe_job(
            repo, claimed, worker_id=worker_id, lease_seconds=lease_seconds
        )

        # Confirm the lease still belongs to us before writing anything. A
        # cancellation or a reclaim during transcription means this result is no
        # longer ours to record.
        if not repo.heartbeat(job_id, worker_id, lease_seconds):
            log_event(
                LOG,
                logging.INFO,
                "lease_lost",
                job_id=job_id,
                source_id=source_id,
                stage="PRE_PERSIST",
            )
            return True

        repo.update_job(job_id, {"stage": "INDEXING", "progress_permille": 930})
        persist_transcription(repo, transcript, rows)
        _run_screen_pass(repo, engine.settings, claimed, worker_id, lease_seconds)
        # Atomic: provenance revalidated, source pointers and terminal status
        # written in one transaction. Raises if the job stopped being RUNNING,
        # so a cancellation that landed mid-persist is never overwritten.
        repo.complete_job(job_id, transcript["transcript_id"])
        log_event(
            LOG,
            logging.INFO,
            "job_succeeded",
            job_id=job_id,
            source_id=source_id,
            transcript_id=transcript["transcript_id"],
        )
    except JobCancelled:
        repo.set_terminal_status(
            job_id,
            {
                "status": "CANCELLED",
                "completed_at": _now(),
                "error_code": None,
                "error_message": None,
            },
        )
        log_event(
            LOG,
            logging.INFO,
            "job_cancelled",
            job_id=job_id,
            source_id=source_id,
        )
    except ValueError as exc:
        log_event(
            LOG,
            logging.WARNING,
            "job_failed",
            job_id=job_id,
            source_id=source_id,
            error_code="INVALID_JOB",
            exception_type=type(exc).__name__,
        )
        repo.set_terminal_status(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now(),
                "error_code": "INVALID_JOB",
                "error_message": "Job configuration or source is not supported.",
            },
        )
    except Exception as exc:
        # Deliberately no LOG.exception / exc_info: exception messages and
        # tracebacks can contain host paths, signed URLs or library internals.
        log_event(
            LOG,
            logging.ERROR,
            "job_failed",
            job_id=job_id,
            source_id=source_id,
            error_code="PROCESSING_FAILED",
            exception_type=type(exc).__name__,
        )
        repo.set_terminal_status(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now(),
                "error_code": "PROCESSING_FAILED",
                "error_message": "Local media processing failed.",
            },
        )
    return True


def _run_screen_pass(
    repo: WatchRepository,
    settings,
    job: dict,
    worker_id: str,
    lease_seconds: int,
) -> None:
    """Read the screen track, if this source has one.

    Runs inside the same job as transcription so one ingest produces both
    tracks on one timeline. An audio-only source skips it entirely and pays
    nothing.
    """
    if not getattr(settings, "screen_enabled", True):
        return

    source = repo.get_source(job["source_id"])
    if not source or not source.get("has_video"):
        return

    from vorquel_watch.local_storage import LocalStorage
    from vorquel_watch.ocr import OcrUnavailable
    from vorquel_watch.screen_pipeline import ScreenCancelled, build_screen_observations

    try:
        repo.update_job(
            job["job_id"], {"stage": "MERGING", "progress_permille": 950}
        )
        storage = LocalStorage(settings.data_dir)
        # SEC-04 applies to the screen pass too: the object is re-hashed before
        # it is read. This sits inside the try because a corrupted object must
        # not cost the transcript either.
        media_path = storage.verify_object(source["content_sha256"])

        result = build_screen_observations(
            media_path,
            source_id=source["source_id"],
            job_id=job["job_id"],
            interval_ms=settings.screen_interval_ms,
            change_threshold=settings.screen_change_threshold,
            max_observations=settings.screen_max_observations,
            max_samples=getattr(settings, "screen_max_samples", 20000),
            keepalive=lambda: repo.heartbeat(job["job_id"], worker_id, lease_seconds),
        )

        repo.insert_screen_observations(result.observations)
        for start in range(0, len(result.text_blocks), 500):
            repo.insert_screen_text_blocks(result.text_blocks[start : start + 500])

        log_event(
            LOG,
            logging.INFO,
            "screen_pass_completed",
            job_id=job["job_id"],
            source_id=source["source_id"],
            observations=result.observations_kept,
            frames_sampled=result.frames_sampled,
            ocr_runs=result.ocr_runs,
            ocr_reused=result.ocr_reused,
            duration_ms=result.duration_ms,
        )
    except ScreenCancelled:
        # Losing the job is not a screen failure: it must stop the whole job.
        raise JobCancelled() from None
    except OcrUnavailable as exc:
        log_event(
            LOG,
            logging.WARNING,
            "screen_pass_degraded",
            job_id=job["job_id"],
            source_id=job["source_id"],
            error_code="OCR_UNAVAILABLE",
            exception_type=type(exc).__name__,
        )
    except Exception as exc:
        # The transcript is the job's primary product and it already succeeded.
        # Failing the whole job here would throw away hours of completed
        # transcription because of a fault in an enhancement.
        log_event(
            LOG,
            logging.ERROR,
            "screen_pass_failed",
            job_id=job["job_id"],
            source_id=job["source_id"],
            error_code="SCREEN_PROCESSING_FAILED",
            exception_type=type(exc).__name__,
        )


def run_worker(
    *,
    once: bool = False,
    poll_seconds: float = 2.0,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> None:
    settings = Settings.from_env()
    repo = WatchRepository(settings)
    repo.healthcheck()
    engine = FasterWhisperEngine(settings)
    worker_id = new_worker_id()
    log_event(
        LOG,
        logging.INFO,
        "worker_started",
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )

    while True:
        worked = process_one(repo, engine, worker_id, lease_seconds)
        if once:
            return
        if not worked:
            time.sleep(max(0.5, poll_seconds))


def main() -> None:
    configure_logging()
    run_worker()
