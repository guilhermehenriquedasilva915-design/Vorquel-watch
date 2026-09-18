from __future__ import annotations

import hashlib
import json
from typing import Any

from vorquel_watch.config import Settings
from vorquel_watch.db import WatchRepository
from vorquel_watch.envelope import envelope, safe_error
from vorquel_watch.exports import create_export


class WatchService:
    def __init__(
        self,
        settings: Settings | None = None,
        repo: WatchRepository | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.repo = repo or WatchRepository(self.settings)

    def _config_hash(
        self,
        *,
        mode: str,
        language_hint: str | None,
    ) -> str:
        payload = {
            "mode": mode,
            "language_hint": language_hint,
            "whisper_model": self.settings.whisper_model,
            "device": self.settings.whisper_device,
            "compute_type": self.settings.whisper_compute_type,
            "pipeline_version": self.settings.pipeline_version,
        }
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get_capabilities(self) -> dict[str, Any]:
        return envelope(
            "get_capabilities",
            {
                "product": "Vorquel Watch",
                "release": "0.1.0-alpha",
                "schema_version": "1.0",
                "pipeline_version": self.settings.pipeline_version,
                "supported_modes": ["FAST"],
                "planned_modes": ["STANDARD", "SPEAKERS"],
                "exports": ["TXT", "MARKDOWN", "JSON", "SRT", "VTT"],
                "search": "POSTGRES_FTS",
                "ingest": {
                    "local_cli": True,
                    "mcp_path_input": False,
                    "url_ingest": False,
                },
                "processing": {
                    "local": True,
                    "raw_media_cloud_upload": False,
                },
            },
            contains_untrusted_content=False,
        )

    def list_sources(
        self,
        limit: int = 50,
        cursor: str | None = None,
        media_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        try:
            data = self.repo.list_sources(
                limit=limit,
                cursor=cursor,
                media_type=media_type,
                status=status,
            )
            return envelope(
                "list_sources",
                data,
                contains_untrusted_content=True,
            )
        except ValueError as exc:
            return safe_error("list_sources", "INVALID_ARGUMENT", str(exc))

    def get_source(self, source_id: str) -> dict[str, Any]:
        row = self.repo.get_source(source_id)
        if not row:
            return safe_error("get_source", "NOT_FOUND", "Source not found.")
        return envelope(
            "get_source",
            row,
            contains_untrusted_content=True,
        )

    def start_analysis(
        self,
        source_id: str,
        mode: str = "FAST",
        language_hint: str | None = None,
    ) -> dict[str, Any]:
        normalized = mode.strip().upper()
        if normalized != "FAST":
            return safe_error(
                "start_analysis",
                "MODE_NOT_AVAILABLE",
                "This alpha currently supports FAST only.",
            )
        try:
            job, reused = self.repo.create_or_reuse_job(
                source_id=source_id,
                mode=normalized,
                language_hint=language_hint.strip() if language_hint else None,
                config_hash=self._config_hash(
                    mode=normalized,
                    language_hint=language_hint,
                ),
            )
            return envelope(
                "start_analysis",
                {
                    "job": job,
                    "reused": reused,
                },
                contains_untrusted_content=False,
            )
        except ValueError as exc:
            return safe_error("start_analysis", "INVALID_SOURCE", str(exc))

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.repo.get_job(job_id)
        if not row:
            return safe_error("get_job", "NOT_FOUND", "Job not found.")
        return envelope("get_job", row, contains_untrusted_content=False)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        row = self.repo.cancel_job(job_id)
        if not row:
            return safe_error("cancel_job", "NOT_FOUND", "Job not found.")
        return envelope("cancel_job", row, contains_untrusted_content=False)

    def get_transcript(
        self,
        transcript_id: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        cursor: str | None = None,
        limit_segments: int = 100,
        include_words: bool = False,
    ) -> dict[str, Any]:
        meta = self.repo.get_transcript_meta(transcript_id)
        if not meta:
            return safe_error(
                "get_transcript",
                "NOT_FOUND",
                "Transcript not found.",
            )
        try:
            segments = self.repo.get_transcript_segments(
                transcript_id,
                start_ms=start_ms,
                end_ms=end_ms,
                cursor=cursor,
                limit=limit_segments,
                include_words=include_words,
            )
        except ValueError as exc:
            return safe_error("get_transcript", "INVALID_ARGUMENT", str(exc))
        return envelope(
            "get_transcript",
            {"transcript": meta, **segments},
            contains_untrusted_content=True,
        )

    def search_transcript(
        self,
        query: str,
        source_ids: list[str],
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        try:
            rows = self.repo.search_segments(
                query=query,
                source_ids=source_ids,
                start_ms=start_ms,
                end_ms=end_ms,
                limit=limit,
            )
        except ValueError as exc:
            return safe_error(
                "search_transcript",
                "INVALID_ARGUMENT",
                str(exc),
            )
        return envelope(
            "search_transcript",
            {"items": rows},
            contains_untrusted_content=True,
        )

    def get_segment(
        self,
        segment_id: str,
        context_before: int = 2,
        context_after: int = 2,
        include_words: bool = False,
    ) -> dict[str, Any]:
        data = self.repo.get_segment(
            segment_id,
            context_before=context_before,
            context_after=context_after,
            include_words=include_words,
        )
        if not data:
            return safe_error("get_segment", "NOT_FOUND", "Segment not found.")
        return envelope(
            "get_segment",
            data,
            contains_untrusted_content=True,
        )

    def list_speakers(self, source_id: str) -> dict[str, Any]:
        return envelope(
            "list_speakers",
            {"items": self.repo.list_speakers(source_id)},
            contains_untrusted_content=True,
        )

    def get_speaker_turns(
        self,
        speaker_id: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            data = self.repo.get_speaker_turns(
                speaker_id,
                start_ms=start_ms,
                end_ms=end_ms,
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            return safe_error(
                "get_speaker_turns",
                "INVALID_ARGUMENT",
                str(exc),
            )
        return envelope(
            "get_speaker_turns",
            data,
            contains_untrusted_content=True,
        )

    def create_export(self, transcript_id: str, format: str) -> dict[str, Any]:
        try:
            artifact = create_export(
                self.repo,
                self.settings,
                transcript_id,
                format,
            )
        except ValueError as exc:
            return safe_error(
                "create_export",
                "INVALID_ARGUMENT",
                str(exc),
            )
        return envelope(
            "create_export",
            {"artifact": artifact},
            contains_untrusted_content=True,
        )

    def list_artifacts(
        self,
        source_id: str,
        artifact_type: str | None = None,
    ) -> dict[str, Any]:
        return envelope(
            "list_artifacts",
            {
                "items": self.repo.list_artifacts(
                    source_id,
                    artifact_type=artifact_type,
                )
            },
            contains_untrusted_content=True,
        )

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.repo.get_artifact(artifact_id)
        if not artifact:
            return safe_error(
                "get_artifact",
                "NOT_FOUND",
                "Artifact not found.",
            )
        return envelope(
            "get_artifact",
            artifact,
            contains_untrusted_content=True,
        )
