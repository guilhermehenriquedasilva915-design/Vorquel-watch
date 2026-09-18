from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from vorquel_watch.config import Settings
from vorquel_watch.db import WatchRepository
from vorquel_watch.transcription import (
    FasterWhisperEngine,
    JobCancelled,
    persist_transcription,
)


LOG = logging.getLogger("vorquel_watch.worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_one(
    repo: WatchRepository,
    engine: FasterWhisperEngine,
) -> bool:
    queued = repo.get_next_queued_job()
    if not queued:
        return False

    claimed = repo.claim_job(queued["job_id"])
    if not claimed:
        return True

    job_id = claimed["job_id"]
    try:
        transcript, rows = engine.transcribe_job(repo, claimed)
        if repo.is_cancelled(job_id):
            return True

        repo.update_job(
            job_id,
            {"stage": "INDEXING", "progress_permille": 930},
        )
        persist_transcription(repo, transcript, rows)
        repo.update_job(
            job_id,
            {
                "status": "SUCCEEDED",
                "stage": "COMPLETE",
                "progress_permille": 1000,
                "completed_at": _now(),
                "error_code": None,
                "error_message": None,
            },
        )
    except JobCancelled:
        repo.update_job(
            job_id,
            {
                "status": "CANCELLED",
                "completed_at": _now(),
                "error_code": None,
                "error_message": None,
            },
        )
    except ValueError as exc:
        LOG.warning("Job rejected: %s", type(exc).__name__)
        repo.update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now(),
                "error_code": "INVALID_JOB",
                "error_message": "Job configuration or source is not supported.",
            },
        )
    except Exception as exc:
        LOG.exception("Job failed (%s)", type(exc).__name__)
        repo.update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now(),
                "error_code": "PROCESSING_FAILED",
                "error_message": "Local media processing failed.",
            },
        )
    return True


def run_worker(*, once: bool = False, poll_seconds: float = 2.0) -> None:
    settings = Settings.from_env()
    repo = WatchRepository(settings)
    repo.healthcheck()
    engine = FasterWhisperEngine(settings)

    while True:
        worked = process_one(repo, engine)
        if once:
            return
        if not worked:
            time.sleep(max(0.5, poll_seconds))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_worker()
