from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path

from vorquel_watch.local_storage import sha256_file


@dataclass(frozen=True, slots=True)
class ProbeResult:
    source_kind: str
    detected_mime: str
    container: str
    duration_ms: int
    has_video: bool
    has_audio: bool
    video_stream_count: int
    audio_stream_count: int


_CONTAINER_MIME = {
    "mov,mp4,m4a,3gp,3g2,mj2": "video/mp4",
    "matroska,webm": "video/webm",
    "avi": "video/x-msvideo",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}


def _import_av():
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "Media support is not installed. Install vorquel-watch[transcribe]."
        ) from exc
    return av


def probe_media(path: Path) -> ProbeResult:
    av = _import_av()
    try:
        with av.open(str(path), mode="r") as container:
            audio_count = sum(1 for stream in container.streams if stream.type == "audio")
            video_count = sum(1 for stream in container.streams if stream.type == "video")
            if audio_count == 0:
                raise ValueError("media contains no audio stream")

            duration_ms = 0
            if container.duration is not None:
                duration_ms = max(
                    0,
                    int(float(container.duration * av.time_base) * 1000),
                )

            format_name = container.format.name or "unknown"
            mime = _CONTAINER_MIME.get(format_name)
            if not mime:
                guessed, _ = mimetypes.guess_type(path.name)
                mime = guessed or "application/octet-stream"

            return ProbeResult(
                source_kind="LOCAL_VIDEO" if video_count else "LOCAL_AUDIO",
                detected_mime=mime,
                container=format_name,
                duration_ms=duration_ms,
                has_video=video_count > 0,
                has_audio=True,
                video_stream_count=video_count,
                audio_stream_count=audio_count,
            )
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise ValueError("file is not supported decodable media") from exc


def validate_and_hash(
    path: Path,
    *,
    max_source_bytes: int,
    max_duration_ms: int,
) -> tuple[str, int, ProbeResult]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("source is not a regular file")

    byte_size = resolved.stat().st_size
    if byte_size <= 0:
        raise ValueError("source is empty")
    if byte_size > max_source_bytes:
        raise ValueError("source exceeds configured byte limit")

    probe = probe_media(resolved)
    if probe.duration_ms <= 0:
        raise ValueError("unable to determine media duration")
    if probe.duration_ms > max_duration_ms:
        raise ValueError("source exceeds configured duration limit")

    return sha256_file(resolved), byte_size, probe
