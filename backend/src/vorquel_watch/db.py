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
            .select("*")
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
                .select("*")
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

    def get_next_queued_job(self) -> dict[str, Any] | None:
        result = (
            self.client.table("processing_jobs")
            .select("*")
            .eq("status", "QUEUED")
            .order("created_at")
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def claim_job(self, job_id: str) -> dict[str, Any] | None:
        result = (
            self.client.table("processing_jobs")
            .update(
                {
                    "status": "RUNNING",
                    "stage": "TRANSCRIBING",
                    "progress_permille": 10,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("job_id", job_id)
            .eq("status", "QUEUED")
            .execute()
        )
        return result.data[0] if result.data else None

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
        source_id: str,
        job_id: str,
    ) -> None:
        self.client.table("transcripts").update(
            {
                "segment_count": segment_count,
                "word_count": word_count,
            }
        ).eq("transcript_id", transcript_id).execute()

        self.client.table("sources").update(
            {
                "latest_transcript_id": transcript_id,
                "latest_successful_job_id": job_id,
            }
        ).eq("source_id", source_id).execute()

    def get_transcript_meta(self, transcript_id: str) -> dict[str, Any] | None:
        result = (
            self.client.table("transcripts")
            .select("*")
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
        columns = "*"
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
        query = (
            self.client.table("transcript_segments")
            .select("*")
            .eq("transcript_id", target["transcript_id"])
            .gte("ordinal", low)
            .lte("ordinal", high)
            .order("ordinal")
        )
        rows = query.execute().data or []
        if not include_words:
            for row in rows:
                row.pop("words", None)
                row.pop("raw_text", None)
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
            .select("*")
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
