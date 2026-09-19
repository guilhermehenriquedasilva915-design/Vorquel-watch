from __future__ import annotations

import hashlib
import json
from typing import Any

from vorquel_watch.config import Settings
from vorquel_watch.db import WatchRepository
from vorquel_watch.envelope import envelope, safe_error
from vorquel_watch.exports import create_export
from vorquel_watch.ids import validate_id


MAX_SEARCH_SOURCE_IDS = 25


def _invalid(tool: str, exc: ValueError) -> dict[str, Any]:
    """Uniform rejection for a malformed argument.

    validate_id never puts the supplied value in its message, so this is safe
    to reflect back to the caller.
    """
    return safe_error(tool, "INVALID_ARGUMENT", str(exc))


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

    def _succeeded_job_for(self, transcript_id: str) -> tuple[dict[str, Any] | None, bool]:
        """Return (transcript_meta, job_succeeded).

        INT-04. A transcript produced by a job that failed, was cancelled or is
        still running is not a final result and must not be served as one.
        """
        meta = self.repo.get_transcript_meta(transcript_id)
        if not meta:
            return None, False
        job = self.repo.get_job(meta.get("job_id"))
        return meta, bool(job and job.get("status") == "SUCCEEDED")

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
                    "resume_running_jobs": False,
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
        try:
            source_id = validate_id(source_id, "src_")
        except ValueError as exc:
            return _invalid("get_source", exc)

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
        try:
            source_id = validate_id(source_id, "src_")
        except ValueError as exc:
            return _invalid("start_analysis", exc)

        normalized = mode.strip().upper() if isinstance(mode, str) else ""
        clean_language = language_hint.strip().lower() if language_hint else None
        if clean_language and (
            len(clean_language) > 16
            or not all(ch.isalnum() or ch == "-" for ch in clean_language)
        ):
            return safe_error(
                "start_analysis",
                "INVALID_ARGUMENT",
                "language_hint must be a short language code.",
            )
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
                language_hint=clean_language,
                config_hash=self._config_hash(
                    mode=normalized,
                    language_hint=clean_language,
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
        try:
            job_id = validate_id(job_id, "job_")
        except ValueError as exc:
            return _invalid("get_job", exc)

        row = self.repo.get_job(job_id)
        if not row:
            return safe_error("get_job", "NOT_FOUND", "Job not found.")
        return envelope("get_job", row, contains_untrusted_content=False)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        try:
            job_id = validate_id(job_id, "job_")
        except ValueError as exc:
            return _invalid("cancel_job", exc)

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
        try:
            transcript_id = validate_id(transcript_id, "trn_")
        except ValueError as exc:
            return _invalid("get_transcript", exc)

        meta, succeeded = self._succeeded_job_for(transcript_id)
        if not meta:
            return safe_error(
                "get_transcript",
                "NOT_FOUND",
                "Transcript not found.",
            )
        if not succeeded:
            return safe_error(
                "get_transcript",
                "TRANSCRIPT_NOT_FINAL",
                "This transcript belongs to a job that has not succeeded.",
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
            if not isinstance(source_ids, list) or not source_ids:
                raise ValueError("source_ids must be a non-empty list")
            if len(source_ids) > MAX_SEARCH_SOURCE_IDS:
                raise ValueError(
                    f"source_ids must contain at most {MAX_SEARCH_SOURCE_IDS} ids"
                )
            source_ids = [validate_id(value, "src_") for value in source_ids]
        except ValueError as exc:
            return _invalid("search_transcript", exc)

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
        try:
            segment_id = validate_id(segment_id, "seg_")
        except ValueError as exc:
            return _invalid("get_segment", exc)

        data = self.repo.get_segment(
            segment_id,
            context_before=context_before,
            context_after=context_after,
            include_words=include_words,
        )
        if not data:
            return safe_error("get_segment", "NOT_FOUND", "Segment not found.")

        # INT-04: a segment is only servable if its transcript is final.
        segments = data.get("segments") or []
        if segments:
            _, succeeded = self._succeeded_job_for(segments[0]["transcript_id"])
            if not succeeded:
                return safe_error(
                    "get_segment",
                    "TRANSCRIPT_NOT_FINAL",
                    "This segment belongs to a job that has not succeeded.",
                )
        return envelope(
            "get_segment",
            data,
            contains_untrusted_content=True,
        )

    def list_speakers(self, source_id: str) -> dict[str, Any]:
        try:
            source_id = validate_id(source_id, "src_")
        except ValueError as exc:
            return _invalid("list_speakers", exc)

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
            speaker_id = validate_id(speaker_id, "spk_")
        except ValueError as exc:
            return _invalid("get_speaker_turns", exc)

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
            transcript_id = validate_id(transcript_id, "trn_")
        except ValueError as exc:
            return _invalid("create_export", exc)

        # INT-04: never export a transcript whose job did not succeed.
        meta, succeeded = self._succeeded_job_for(transcript_id)
        if not meta:
            return safe_error(
                "create_export",
                "NOT_FOUND",
                "Transcript not found.",
            )
        if not succeeded:
            return safe_error(
                "create_export",
                "TRANSCRIPT_NOT_FINAL",
                "This transcript belongs to a job that has not succeeded.",
            )

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
        try:
            source_id = validate_id(source_id, "src_")
        except ValueError as exc:
            return _invalid("list_artifacts", exc)

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
        try:
            artifact_id = validate_id(artifact_id, "art_")
        except ValueError as exc:
            return _invalid("get_artifact", exc)

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
