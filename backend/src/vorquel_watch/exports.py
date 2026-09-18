from __future__ import annotations

import hashlib
import json
from typing import Any

from vorquel_watch.config import Settings
from vorquel_watch.db import WatchRepository
from vorquel_watch.ids import new_id
from vorquel_watch.local_storage import LocalStorage


_FORMATS = {
    "TXT": ("TRANSCRIPT_TXT", ".txt", "text/plain"),
    "MARKDOWN": ("MARKDOWN", ".md", "text/markdown"),
    "JSON": ("TRANSCRIPT_JSON", ".json", "application/json"),
    "SRT": ("SRT", ".srt", "application/x-subrip"),
    "VTT": ("VTT", ".vtt", "text/vtt"),
}


def _all_segments(
    repo: WatchRepository,
    transcript_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = repo.get_transcript_segments(
            transcript_id,
            cursor=cursor,
            limit=200,
            include_words=False,
        )
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return rows


def _stamp(ms: int, separator: str) -> str:
    total_ms = max(0, int(ms))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _render(fmt: str, transcript: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    if fmt == "TXT":
        return "\n".join(row["effective_text"] for row in rows).encode("utf-8")

    if fmt == "MARKDOWN":
        lines = [
            f"# Transcript {transcript['transcript_id']}",
            "",
            f"Source: {transcript['source_id']}",
            "",
        ]
        for row in rows:
            lines.append(
                f"- [{_stamp(row['start_ms'], '.')}] {row['effective_text']}"
            )
        return "\n".join(lines).encode("utf-8")

    if fmt == "JSON":
        payload = {
            "schema_version": "1.0",
            "transcript": transcript,
            "segments": rows,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

    if fmt == "SRT":
        blocks = []
        for index, row in enumerate(rows, start=1):
            blocks.append(
                f"{index}\n"
                f"{_stamp(row['start_ms'], ',')} --> {_stamp(row['end_ms'], ',')}\n"
                f"{row['effective_text']}"
            )
        return ("\n\n".join(blocks) + "\n").encode("utf-8")

    if fmt == "VTT":
        lines = ["WEBVTT", ""]
        for row in rows:
            lines.extend(
                [
                    f"{_stamp(row['start_ms'], '.')} --> {_stamp(row['end_ms'], '.')}",
                    row["effective_text"],
                    "",
                ]
            )
        return "\n".join(lines).encode("utf-8")

    raise ValueError("unsupported export format")


def create_export(
    repo: WatchRepository,
    settings: Settings,
    transcript_id: str,
    fmt: str,
) -> dict[str, Any]:
    normalized = fmt.strip().upper()
    if normalized not in _FORMATS:
        raise ValueError("unsupported export format")

    transcript = repo.get_transcript_meta(transcript_id)
    if not transcript:
        raise ValueError("transcript not found")

    rows = _all_segments(repo, transcript_id)
    content = _render(normalized, transcript, rows)
    artifact_type, suffix, mime = _FORMATS[normalized]
    artifact_id = new_id("art_")

    storage = LocalStorage(settings.data_dir)
    target = storage.export_path(artifact_id, suffix)
    target.write_bytes(content)

    payload = {
        "artifact_id": artifact_id,
        "schema_version": "1.0",
        "source_id": transcript["source_id"],
        "job_id": transcript["job_id"],
        "artifact_type": artifact_type,
        "mime": mime,
        "byte_size": len(content),
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
        "retention_class": "USER_EXPORT",
        "data_trust_class": "UNTRUSTED_DERIVED",
        "instruction_authority": "NONE",
    }
    return repo.create_artifact(payload)
