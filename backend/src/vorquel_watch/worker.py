from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from vorquel_watch.config import Settings
from vorquel_watch.db import DEFAULT_LEASE_SECONDS, WatchRepository
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
    try:
        transcript, rows = engine.transcribe_job(
            repo, claimed, worker_id=worker_id, lease_seconds=lease_seconds
        )

        # Confirm the lease still belongs to us before writing anything. A
        # cancellation or a reclaim during transcription means this result is no
        # longer ours to record.
        if not repo.heartbeat(job_id, worker_id, lease_seconds):
            LOG.info("Lease lost before persist; discarding result")
            return True

        repo.update_job(job_id, {"stage": "INDEXING", "progress_permille": 930})
        persist_transcription(repo, transcript, rows)
        # Atomic: provenance revalidated, source pointers and terminal status
        # written in one transaction. Raises if the job stopped being RUNNING,
        # so a cancellation that landed mid-persist is never overwritten.
        repo.complete_job(job_id, transcript["transcript_id"])
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
    except ValueError as exc:
        LOG.warning("Job rejected: %s", type(exc).__name__)
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
        LOG.exception("Job failed (%s)", type(exc).__name__)
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
    LOG.info("Worker started (lease %ss)", lease_seconds)

    while True:
        worked = process_one(repo, engine, worker_id, lease_seconds)
        if once:
            return
        if not worked:
            time.sleep(max(0.5, poll_seconds))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_worker()
