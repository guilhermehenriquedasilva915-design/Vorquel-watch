from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from supabase import Client, create_client

from vorquel_watch.config import Settings
from vorquel_watch.ids import new_id


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        offset = int(value["offset"])
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if offset < 0:
        raise ValueError("invalid cursor")
    return offset


def _clamp_limit(value: int, maximum: int = 200) -> int:
    return max(1, min(int(value), maximum))


# How long a claimed job stays this worker's before another may reclaim it.
# Long enough that an ordinary pause between heartbeats is not mistaken for a
# dead worker, short enough that a crash does not strand a job for long.
DEFAULT_LEASE_SECONDS = 120


# SEC-07. The explicit projection for segment data leaving the process toward
# an MCP client. raw_text is deliberately absent: it is the pre-review original
# and is not part of the MCP surface, callers receive effective_text. words is
# appended only when the caller asks for it. Listing columns rather than
# selecting * means a future internal column is not exposed by accident.
_SEGMENT_MCP_COLUMNS = (
    "segment_id,transcript_id,source_id,ordinal,start_ms,end_ms,"
    "effective_text,text_origin,review_status,revision,speaker_id,"
    "confidence,data_trust_class,instruction_authority,provenance"
)

# Job rows are returned to MCP callers by start_analysis and get_job.
_JOB_MCP_COLUMNS = (
    "job_id,source_id,mode,status,stage,progress_permille,pipeline_version,"
    "language_hint,resume_capable,error_code,error_message,created_at,"
    "started_at,completed_at"
)


class WatchRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_secret_key,
        )

    def healthcheck(self) -> None:
        self.client.table("sources").select("source_id").limit(1).execute()

    def find_source_by_hash(self, content_sha256: str) -> dict[str, Any] | None:
        result = (
            self.client.table("sources")
            .select("*")
            .eq("content_sha256", content_sha256)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def create_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.find_source_by_hash(payload["content_sha256"])
        if existing:
            return existing
        try:
            result = self.client.table("sources").insert(payload).execute()
            return result.data[0]
        except Exception:
            existing = self.find_source_by_hash(payload["content_sha256"])
            if existing:
                return existing
            raise

    def list_sources(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        media_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        limit = _clamp_limit(limit, 100)
        offset = _decode_cursor(cursor)

        query = (
            self.client.table("sources")
            .select(
                "source_id,source_kind,content_sha256,byte_size,detected_mime,"
                "duration_ms,ingest_status,has_video,has_audio,"
                "latest_transcript_id,latest_successful_job_id,created_at,"
                "data_trust_class,instruction_authority"
            )
            .order("created_at", desc=True)
        )
        if media_type:
            normalized = media_type.strip().upper()
            mapping = {
                "VIDEO": "LOCAL_VIDEO",
                "AUDIO": "LOCAL_AUDIO",
                "LOCAL_VIDEO": "LOCAL_VIDEO",
                "LOCAL_AUDIO": "LOCAL_AUDIO",
            }
            if normalized not in mapping:
                raise ValueError("invalid media_type")
            query = query.eq("source_kind", mapping[normalized])
        if status:
            query = query.eq("ingest_status", status.strip().upper())

        result = query.range(offset, offset + limit - 1).execute()
        rows = result.data or []
        return {
            "items": rows,
            "next_cursor": _encode_cursor(offset + len(rows)) if len(rows) == limit else None,
        }

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        result = (
            self.client.table("sources")
            .select(
                "source_id,source_kind,content_sha256,byte_size,detected_mime,"
                "duration_ms,ingest_status,container,has_video,has_audio,"
                "video_stream_count,audio_stream_count,external_metadata,"
                "latest_transcript_id,latest_successful_job_id,created_at,"
                "security_policy_version,data_trust_class,instruction_authority"
            )
            .eq("source_id", source_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def create_or_reuse_job(
        self,
        *,
        source_id: str,
        mode: str,
        language_hint: str | None,
        config_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        source = self.get_source(source_id)
        if not source or source["ingest_status"] != "READY":
            raise ValueError("source is not ready")

        existing = (
            self.client.table("processing_jobs")
            .select(_JOB_MCP_COLUMNS)
            .eq("source_id", source_id)
            .eq("mode", mode)
            .eq("config_hash", config_hash)
            .eq("pipeline_version", self.settings.pipeline_version)
            .in_("status", ["QUEUED", "RUNNING", "SUCCEEDED"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            return existing.data[0], True

        payload = {
            "job_id": new_id("job_"),
            "source_id": source_id,
            "mode": mode,
            "status": "QUEUED",
            "stage": "QUEUED",
            "progress_permille": 0,
            "pipeline_version": self.settings.pipeline_version,
            "config_hash": config_hash,
            "language_hint": language_hint,
            "resume_capable": False,
        }
        try:
            result = self.client.table("processing_jobs").insert(payload).execute()
            return result.data[0], False
        except Exception:
            existing = (
                self.client.table("processing_jobs")
                .select(_JOB_MCP_COLUMNS)
                .eq("source_id", source_id)
                .eq("mode", mode)
                .eq("config_hash", config_hash)
                .eq("pipeline_version", self.settings.pipeline_version)
                .in_("status", ["QUEUED", "RUNNING", "SUCCEEDED"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if existing.data:
                return existing.data[0], True
            raise

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        result = (
            self.client.table("processing_jobs")
            .select(
                "job_id,source_id,mode,status,stage,progress_permille,"
                "pipeline_version,language_hint,resume_capable,error_code,"
                "error_message,created_at,started_at,completed_at"
            )
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """Take the next queued job, or reclaim one whose worker died.

        The database picks and locks the row with FOR UPDATE SKIP LOCKED, so two
        workers never receive the same job, and a RUNNING job whose lease has
        expired becomes available again. Without this a worker that died
        mid-persist left the job RUNNING forever and create_or_reuse_job kept
        handing that dead job back.
        """
        result = self.client.rpc(
            "claim_next_job",
            {"p_worker_id": worker_id, "p_lease_seconds": int(lease_seconds)},
        ).execute()

        job = result.data
        if isinstance(job, list):
            job = job[0] if job else None
        # An empty queue comes back as a composite whose columns are all NULL,
        # so presence of the row is not enough: the identifier decides.
        if not isinstance(job, dict) or not job.get("job_id"):
            return None
        return job

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend this worker's lease. False means stop working on the job.

        False covers every way a worker can lose a job: the lease expired and
        another worker reclaimed it, or the job was cancelled or finished. The
        caller must not keep processing after a false.
        """
        result = self.client.rpc(
            "heartbeat_job",
            {
                "p_job_id": job_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": int(lease_seconds),
            },
        ).execute()
        return bool(result.data)

    def update_job(
        self,
        job_id: str,
        values: dict[str, Any],
    ) -> None:
        self.client.table("processing_jobs").update(values).eq(
            "job_id", job_id
        ).execute()

    def is_cancelled(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job["status"] == "CANCELLED")

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        current = self.get_job(job_id)
        if not current:
            return None
        if current["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return current

        result = (
            self.client.table("processing_jobs")
            .update(
                {
                    "status": "CANCELLED",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_code": None,
                    "error_message": None,
                }
            )
            .eq("job_id", job_id)
            .in_("status", ["QUEUED", "RUNNING"])
            .execute()
        )
        return result.data[0] if result.data else self.get_job(job_id)

    def create_transcript(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.client.table("transcripts").insert(payload).execute()
        return result.data[0]

    def insert_segments(self, rows: Iterable[dict[str, Any]]) -> None:
        payload = list(rows)
        if payload:
            self.client.table("transcript_segments").insert(payload).execute()

    def finish_transcript(
        self,
        *,
        transcript_id: str,
        segment_count: int,
        word_count: int,
    ) -> None:
        """Write transcript totals only.

        The source pointers and the terminal job status are set by complete_job
        in a single transaction, never piecemeal from here.
        """
        self.client.table("transcripts").update(
            {
                "segment_count": segment_count,
                "word_count": word_count,
            }
        ).eq("transcript_id", transcript_id).execute()

    def complete_job(self, job_id: str, transcript_id: str) -> None:
        """Finalize a job atomically (INT-02).

        The database function revalidates provenance and the segment count,
        sets the source pointers and marks the job SUCCEEDED in one
        transaction. It raises if the job is no longer RUNNING, which is how a
        concurrent cancellation wins the race (INT-03).
        """
        self.client.rpc(
            "complete_processing_job",
            {"p_job_id": job_id, "p_transcript_id": transcript_id},
        ).execute()

    def set_terminal_status(self, job_id: str, values: dict[str, Any]) -> bool:
        """Move a non-terminal job to a terminal state. Returns whether it applied.

        The status predicate is what makes this safe: a job that already reached
        CANCELLED matches no row, so this is a no-op instead of a state-machine
        violation raised from inside an exception handler, which would otherwise
        propagate out of the worker's error path and kill the loop.
        """
        result = (
            self.client.table("processing_jobs")
            .update(values)
            .eq("job_id", job_id)
            .in_("status", ["QUEUED", "RUNNING"])
            .execute()
        )
        return bool(result.data)

    def get_transcript_meta(self, transcript_id: str) -> dict[str, Any] | None:
        """SEC-07: explicit projection. job_id is included so the caller can
        check that the producing job actually succeeded (INT-04)."""
        result = (
            self.client.table("transcripts")
            .select(
                "transcript_id,source_id,job_id,language,text_source,"
                "segment_count,word_count,duration_ms,alignment,diarization,"
                "engine,data_trust_class,instruction_authority,created_at"
            )
            .eq("transcript_id", transcript_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_transcript_segments(
        self,
        transcript_id: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
        include_words: bool = False,
    ) -> dict[str, Any]:
        limit = _clamp_limit(limit, 200)
        offset = _decode_cursor(cursor)
        columns = (
            "segment_id,transcript_id,source_id,ordinal,start_ms,end_ms,"
            "effective_text,text_origin,review_status,revision,speaker_id,"
            "confidence,data_trust_class,instruction_authority,provenance"
        )
        if include_words:
            columns += ",words"

        query = (
            self.client.table("transcript_segments")
            .select(columns)
            .eq("transcript_id", transcript_id)
            .order("ordinal")
        )
        if start_ms is not None:
            query = query.gte("end_ms", max(0, int(start_ms)))
        if end_ms is not None:
            query = query.lte("start_ms", max(0, int(end_ms)))

        result = query.range(offset, offset + limit - 1).execute()
        rows = result.data or []
        return {
            "items": rows,
            "next_cursor": _encode_cursor(offset + len(rows)) if len(rows) == limit else None,
        }

    def search_segments(
        self,
        *,
        query: str,
        source_ids: list[str],
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("query is required")
        if not source_ids or len(source_ids) > 25:
            raise ValueError("source_ids must contain between 1 and 25 ids")
        limit = _clamp_limit(limit, 50)

        result = self.client.rpc(
            "search_transcript_segments",
            {
                "p_query": clean_query[:500],
                "p_source_ids": source_ids,
                "p_start_ms": start_ms,
                "p_end_ms": end_ms,
                "p_limit": limit,
            },
        ).execute()
        return result.data or []

    def get_segment(
        self,
        segment_id: str,
        *,
        context_before: int = 2,
        context_after: int = 2,
        include_words: bool = False,
    ) -> dict[str, Any] | None:
        """Read one segment plus bounded neighbouring context.

        SEC-07. The projection is explicit in both queries. The previous
        select("*") leaked raw_text whenever include_words was true, because
        the pop that removed it only ran on the false branch - and it would
        have leaked any future internal column automatically. raw_text is the
        pre-review original and is never part of the MCP surface; callers get
        effective_text. Adding a column here is now a deliberate act.
        """
        columns = _SEGMENT_MCP_COLUMNS
        if include_words:
            columns += ",words"

        result = (
            self.client.table("transcript_segments")
            .select(columns)
            .eq("segment_id", segment_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None

        target = result.data[0]
        before = max(0, min(int(context_before), 10))
        after = max(0, min(int(context_after), 10))
        low = max(0, int(target["ordinal"]) - before)
        high = int(target["ordinal"]) + after

        rows = (
            self.client.table("transcript_segments")
            .select(columns)
            .eq("transcript_id", target["transcript_id"])
            .gte("ordinal", low)
            .lte("ordinal", high)
            .order("ordinal")
            .execute()
            .data
            or []
        )
        return {"target_segment_id": segment_id, "segments": rows}

    def list_speakers(self, source_id: str) -> list[dict[str, Any]]:
        result = (
            self.client.table("speakers")
            .select(
                "speaker_id,source_id,label,display_name,name_source,"
                "review_status,data_trust_class,instruction_authority,created_at"
            )
            .eq("source_id", source_id)
            .order("created_at")
            .execute()
        )
        return result.data or []

    def get_speaker_turns(
        self,
        speaker_id: str,
        *,
        start_ms: int | None,
        end_ms: int | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        limit = _clamp_limit(limit, 200)
        offset = _decode_cursor(cursor)
        query = (
            self.client.table("speaker_turns")
            .select(
                "turn_id,speaker_id,source_id,job_id,start_ms,end_ms,"
                "confidence,created_at"
            )
            .eq("speaker_id", speaker_id)
            .order("start_ms")
        )
        if start_ms is not None:
            query = query.gte("end_ms", max(0, int(start_ms)))
        if end_ms is not None:
            query = query.lte("start_ms", max(0, int(end_ms)))
        rows = query.range(offset, offset + limit - 1).execute().data or []
        return {
            "items": rows,
            "next_cursor": _encode_cursor(offset + len(rows)) if len(rows) == limit else None,
        }

    def create_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.client.table("artifacts").insert(payload).execute()
        return result.data[0]

    def list_artifacts(
        self,
        source_id: str,
        artifact_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            self.client.table("artifacts")
            .select(
                "artifact_id,source_id,job_id,artifact_type,mime,byte_size,"
                "artifact_sha256,retention_class,data_trust_class,"
                "instruction_authority,created_at"
            )
            .eq("source_id", source_id)
            .order("created_at", desc=True)
        )
        if artifact_type:
            query = query.eq("artifact_type", artifact_type)
        return query.execute().data or []

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        result = (
            self.client.table("artifacts")
            .select(
                "artifact_id,source_id,job_id,artifact_type,mime,byte_size,"
                "artifact_sha256,retention_class,data_trust_class,"
                "instruction_authority,created_at"
            )
            .eq("artifact_id", artifact_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
