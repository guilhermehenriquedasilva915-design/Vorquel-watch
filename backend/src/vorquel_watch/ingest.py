from __future__ import annotations

import shutil
from pathlib import Path

from vorquel_watch.config import Settings
from vorquel_watch.db import WatchRepository
from vorquel_watch.ids import new_id
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.source_guard import validate_and_hash


def _safe_filename(name: str) -> str:
    cleaned = "".join(
        ch for ch in name
        if ch.isprintable() and ch not in "\r\n\t"
    ).strip()
    return cleaned[:200] or "media"


def _register(
    source_path: Path,
    settings: Settings,
    *,
    external_metadata: dict,
) -> dict:
    """Validate, hash, store and register a media file.

    Shared by every ingest route. A file that arrived from YouTube goes through
    exactly the same Source Guard as one picked off the disk: the origin is
    recorded as metadata, never as a reason to trust the bytes.
    """
    content_sha256, byte_size, probe = validate_and_hash(
        source_path,
        max_source_bytes=settings.max_source_bytes,
        max_duration_ms=settings.max_duration_ms,
    )

    repo = WatchRepository(settings)
    existing = repo.find_source_by_hash(content_sha256)
    if existing:
        return {
            "source_id": existing["source_id"],
            "reused": True,
            "duration_ms": existing["duration_ms"],
            "source_kind": existing["source_kind"],
        }

    storage = LocalStorage(settings.data_dir)
    storage.store_source(source_path, content_sha256)

    payload = {
        "source_id": new_id("src_"),
        "schema_version": "1.0",
        "source_kind": probe.source_kind,
        "content_sha256": content_sha256,
        "byte_size": byte_size,
        "detected_mime": probe.detected_mime,
        "duration_ms": probe.duration_ms,
        "ingest_status": "READY",
        "container": probe.container,
        "has_video": probe.has_video,
        "has_audio": probe.has_audio,
        "video_stream_count": probe.video_stream_count,
        "audio_stream_count": probe.audio_stream_count,
        "external_metadata": external_metadata,
        "security_policy_version": "source-guard/1",
        "data_trust_class": "UNTRUSTED_MEDIA",
        "instruction_authority": "NONE",
    }
    created = repo.create_source(payload)
    return {
        "source_id": created["source_id"],
        "reused": created["source_id"] != payload["source_id"],
        "duration_ms": created["duration_ms"],
        "source_kind": created["source_kind"],
    }


def ingest_local_file(path: str, settings: Settings) -> dict:
    """Ingest from the local control plane, never from an MCP tool."""
    source_path = Path(path).expanduser().resolve(strict=True)
    return _register(
        source_path,
        settings,
        external_metadata={"original_filename": _safe_filename(source_path.name)},
    )


def ingest_youtube_url(url: str, settings: Settings) -> dict:
    """Fetch a single YouTube video and ingest it like any local file.

    The URL is typed into the local control plane. It is never an MCP argument,
    so Claude cannot ask the Watch to fetch anything.

    Downloaded bytes are untrusted exactly like a file from disk: the same
    Source Guard validates the container, codecs and duration, and the title
    and uploader are stored as metadata with no authority of their own.
    """
    from vorquel_watch.youtube import canonical_video_url, download

    canonical = canonical_video_url(url)
    workspace = (settings.data_dir / "cache" / "youtube").resolve()

    try:
        media_path, remote = download(
            canonical,
            workspace,
            max_duration_s=settings.max_duration_ms // 1000,
            max_bytes=settings.max_source_bytes,
        )
        result = _register(
            media_path,
            settings,
            external_metadata={
                "origin": "youtube",
                "video_id": remote.video_id,
                # Title and uploader come from the remote service and are
                # media-derived text: data, not instructions.
                "title": _safe_filename(remote.title),
                "uploader": _safe_filename(remote.uploader or ""),
                "webpage_url": remote.webpage_url,
                "announced_duration_s": remote.duration_s,
            },
        )
    finally:
        # The download is a working copy. Content-addressed storage holds the
        # canonical object, so the cache directory is not left to grow.
        shutil.rmtree(workspace, ignore_errors=True)

    result["origin"] = "youtube"
    return result
