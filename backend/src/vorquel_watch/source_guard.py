from __future__ import annotations

import json
import os
import subprocess
import sys
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
    max_video_width: int
    max_video_height: int
    max_audio_sample_rate: int
    max_audio_channels: int


# Strict container allowlist. An unlisted container is rejected outright: we do
# not enable a format merely because FFmpeg can demux it.
#
# AVI is deliberately absent. The MagicYUV heap out-of-bounds write class of
# FFmpeg bugs (CVE-2026-8461, "PixelSmash", CVSS 8.8) is reached through the
# AVI/MKV/MOV demuxers. AVI carries the widest legacy codec surface and the
# least product value here, so it stays blocked until an explicit architecture
# decision says otherwise.
_ALLOWED_CONTAINERS = {
    "mov,mp4,m4a,3gp,3g2,mj2": "video/mp4",
    "matroska,webm": "video/webm",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}

# These are decoder *implementation* names as PyAV reports them, which are not
# always the codec name: MP3 decodes as "mp3float", so listing only "mp3"
# rejected every MP3 file. Measured against the pinned runtime; the container
# round-trip test is what keeps this set honest when the runtime changes.
# ("vorbis" is listed but unverified: this build has no libvorbis encoder, so
# no fixture could be produced for it.)
_ALLOWED_AUDIO_CODECS = frozenset(
    {
        "aac",
        "mp3",
        "mp3float",
        "flac",
        "opus",
        "vorbis",
        "pcm_s16le",
        "pcm_s24le",
        "pcm_f32le",
    }
)

_ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9", "av1", "mpeg4"})

_MAX_STREAMS = 8

# SEC-02 limits on the isolated parse.
PROBE_TIMEOUT_SECONDS = int(os.environ.get("VORQUEL_WATCH_PROBE_TIMEOUT", "60"))
_MAX_PROBE_OUTPUT_BYTES = 64 * 1024

# The child needs an interpreter and a temp directory, nothing else. Every other
# variable - above all the Supabase credential - is withheld, so a compromised
# parse cannot read a secret out of its own environment.
_CHILD_ENV_PASSTHROUGH = (
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)


def _child_env() -> dict[str, str]:
    env = {
        name: os.environ[name]
        for name in _CHILD_ENV_PASSTHROUGH
        if name in os.environ
    }
    # Keep the child from importing anything the parent did not install.
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_probe(path: Path) -> dict:
    """Parse media in a separate process (SEC-02).

    Explicit argv, no shell, no inherited environment beyond the interpreter's
    needs, a wall-clock timeout, a capped stdout, and a closed stdin.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "vorquel_watch.media_probe", str(path)],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=_child_env(),
            cwd=str(path.parent),
            stdin=subprocess.DEVNULL,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("media probe exceeded its time limit") from None
    except OSError:
        raise RuntimeError("could not start the media probe process") from None

    if len(completed.stdout) > _MAX_PROBE_OUTPUT_BYTES:
        raise ValueError("media probe produced too much output")

    if completed.returncode != 0:
        # A crash in the parse is a rejection, not an incident the caller can act
        # on. stderr is discarded: it carries FFmpeg text and the input path.
        raise ValueError("file is not supported decodable media")

    try:
        payload = json.loads(completed.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise ValueError("media probe returned malformed output") from None

    if not isinstance(payload, dict):
        raise ValueError("media probe returned malformed output")
    return payload


def probe_media(
    path: Path,
    *,
    max_video_width: int = 3840,
    max_video_height: int = 2160,
    max_audio_sample_rate: int = 192000,
    max_audio_channels: int = 8,
) -> ProbeResult:
    """Validate media against policy, parsing it out of process.

    The parse happens in a child; every policy decision below happens here, in
    the trusted parent, on plain data the child returned.
    """
    payload = _run_probe(path)

    if not payload.get("ok"):
        if payload.get("error") == "media_runtime_missing":
            raise RuntimeError(
                "Media support is not installed. Install vorquel-watch[transcribe]."
            )
        raise ValueError("file is not supported decodable media")

    format_name = str(payload.get("format") or "unknown")
    mime = _ALLOWED_CONTAINERS.get(format_name)
    if mime is None:
        raise ValueError("container format is not allowed")

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("media probe returned malformed output")
    if len(streams) > _MAX_STREAMS:
        raise ValueError("media declares too many streams")

    audio_streams = [s for s in streams if isinstance(s, dict) and s.get("type") == "audio"]
    video_streams = [s for s in streams if isinstance(s, dict) and s.get("type") == "video"]
    if not audio_streams:
        raise ValueError("media contains no audio stream")

    audio_sample_rates: list[int] = []
    audio_channels: list[int] = []
    for stream in audio_streams:
        if stream.get("codec") not in _ALLOWED_AUDIO_CODECS:
            raise ValueError("audio codec is not allowed")
        try:
            sample_rate = max(0, int(stream.get("sample_rate") or 0))
            channels = max(0, int(stream.get("channels") or 0))
        except (TypeError, ValueError):
            raise ValueError("media probe returned malformed stream metadata") from None
        if sample_rate > max_audio_sample_rate:
            raise ValueError("audio sample rate exceeds configured limit")
        if channels > max_audio_channels:
            raise ValueError("audio channel count exceeds configured limit")
        audio_sample_rates.append(sample_rate)
        audio_channels.append(channels)

    video_widths: list[int] = []
    video_heights: list[int] = []
    for stream in video_streams:
        if stream.get("codec") not in _ALLOWED_VIDEO_CODECS:
            raise ValueError("video codec is not allowed")
        try:
            width = max(0, int(stream.get("width") or 0))
            height = max(0, int(stream.get("height") or 0))
        except (TypeError, ValueError):
            raise ValueError("media probe returned malformed stream metadata") from None
        if width > max_video_width or height > max_video_height:
            raise ValueError("video resolution exceeds configured limit")
        video_widths.append(width)
        video_heights.append(height)

    # duration_raw is in AV_TIME_BASE units (microseconds), so it must be
    # divided by time_base, not multiplied. The original multiplication inflated
    # every duration by 1e6 and made validate_and_hash reject 100% of valid
    # media.
    duration_ms = 0
    raw = payload.get("duration_raw")
    time_base = payload.get("time_base") or 1_000_000
    if isinstance(raw, int) and time_base:
        duration_ms = max(0, int((raw / time_base) * 1000))

    return ProbeResult(
        source_kind="LOCAL_VIDEO" if video_streams else "LOCAL_AUDIO",
        detected_mime=mime,
        container=format_name,
        duration_ms=duration_ms,
        has_video=bool(video_streams),
        has_audio=True,
        video_stream_count=len(video_streams),
        audio_stream_count=len(audio_streams),
        max_video_width=max(video_widths, default=0),
        max_video_height=max(video_heights, default=0),
        max_audio_sample_rate=max(audio_sample_rates, default=0),
        max_audio_channels=max(audio_channels, default=0),
    )


def validate_and_hash(
    path: Path,
    *,
    max_source_bytes: int,
    max_duration_ms: int,
    max_video_width: int = 3840,
    max_video_height: int = 2160,
    max_audio_sample_rate: int = 192000,
    max_audio_channels: int = 8,
) -> tuple[str, int, ProbeResult]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("source is not a regular file")

    byte_size = resolved.stat().st_size
    if byte_size <= 0:
        raise ValueError("source is empty")
    if byte_size > max_source_bytes:
        raise ValueError("source exceeds configured byte limit")

    probe = probe_media(
        resolved,
        max_video_width=max_video_width,
        max_video_height=max_video_height,
        max_audio_sample_rate=max_audio_sample_rate,
        max_audio_channels=max_audio_channels,
    )
    if probe.duration_ms <= 0:
        raise ValueError("unable to determine media duration")
    if probe.duration_ms > max_duration_ms:
        raise ValueError("source exceeds configured duration limit")

    return sha256_file(resolved), byte_size, probe
