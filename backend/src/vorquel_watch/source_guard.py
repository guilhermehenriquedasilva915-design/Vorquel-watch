from __future__ import annotations

from dataclasses import dataclass
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


# Strict container allowlist. An unlisted container is rejected outright: we do
# not enable a format merely because FFmpeg can demux it.
#
# AVI is deliberately absent. The MagicYUV heap out-of-bounds write class of
# FFmpeg bugs (CVE-2026-8461, "PixelSmash", CVSS 8.8) is reached through the
# AVI/MKV/MOV demuxers. AVI carries the widest legacy codec surface and the
# least product value here, so it stays blocked until a sandboxed probe and an
# explicit architecture decision say otherwise.
_ALLOWED_CONTAINERS = {
    "mov,mp4,m4a,3gp,3g2,mj2": "video/mp4",
    "matroska,webm": "video/webm",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}

_ALLOWED_AUDIO_CODECS = frozenset(
    {"aac", "mp3", "flac", "opus", "vorbis", "pcm_s16le", "pcm_s24le", "pcm_f32le"}
)

_ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9", "av1", "mpeg4"})

_MAX_STREAMS = 8


def _import_av():
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "Media support is not installed. Install vorquel-watch[transcribe]."
        ) from exc
    return av


def _codec_name(stream) -> str:
    context = getattr(stream, "codec_context", None)
    name = getattr(context, "name", None)
    if name:
        return str(name)
    codec = getattr(context, "codec", None)
    return str(getattr(codec, "name", "") or "")


def probe_media(path: Path) -> ProbeResult:
    av = _import_av()
    try:
        with av.open(str(path), mode="r") as container:
            format_name = container.format.name or "unknown"
            mime = _ALLOWED_CONTAINERS.get(format_name)
            if mime is None:
                raise ValueError("container format is not allowed")

            streams = list(container.streams)
            if len(streams) > _MAX_STREAMS:
                raise ValueError("media declares too many streams")

            audio_streams = [s for s in streams if s.type == "audio"]
            video_streams = [s for s in streams if s.type == "video"]
            if not audio_streams:
                raise ValueError("media contains no audio stream")

            for stream in audio_streams:
                if _codec_name(stream) not in _ALLOWED_AUDIO_CODECS:
                    raise ValueError("audio codec is not allowed")
            for stream in video_streams:
                if _codec_name(stream) not in _ALLOWED_VIDEO_CODECS:
                    raise ValueError("video codec is not allowed")

            # container.duration is expressed in AV_TIME_BASE units
            # (microseconds), so it must be divided by av.time_base, not
            # multiplied. The previous multiplication inflated every duration by
            # 1e6 and made validate_and_hash reject 100% of valid media.
            duration_ms = 0
            if container.duration is not None:
                duration_ms = max(0, int((container.duration / av.time_base) * 1000))

            return ProbeResult(
                source_kind="LOCAL_VIDEO" if video_streams else "LOCAL_AUDIO",
                detected_mime=mime,
                container=format_name,
                duration_ms=duration_ms,
                has_video=bool(video_streams),
                has_audio=True,
                video_stream_count=len(video_streams),
                audio_stream_count=len(audio_streams),
            )
    except av.error.FFmpegError:
        # FFmpeg error strings embed the absolute host path of the input file
        # (and several PyAV errors subclass ValueError, so they would otherwise
        # pass straight through the re-raise below). Replace the message and
        # drop the cause so no host path reaches a caller, a log or the MCP.
        raise ValueError("file is not supported decodable media") from None
    except (ValueError, RuntimeError):
        raise
    except Exception:
        raise ValueError("file is not supported decodable media") from None


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
