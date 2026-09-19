"""Controlled YouTube ingest.

This is the only component that reaches the public network, so it is the only
one that can be pointed at an attacker-chosen destination. Everything here
exists to narrow that: the URL must be HTTPS, must be YouTube, must name a
single video, and the download is bounded in size, duration and time.

A URL never arrives through an MCP tool. It is typed by the operator into the
local control plane, exactly like a file path is. Claude cannot ask the Watch
to fetch anything.

yt-dlp is invoked as a separate process with an explicit argument list and
--ignore-config, so no user or system yt-dlp configuration file can inject
options we did not choose. Browser cookies are never read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


# Hosts that serve YouTube videos. Anything else is refused: this is an
# allowlist, not a blocklist, so a new lookalike domain does not silently work.
ALLOWED_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)

DEFAULT_MAX_DURATION_S = 6 * 60 * 60
DEFAULT_MAX_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_TIMEOUT_S = 60 * 60

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


class YouTubeError(RuntimeError):
    """Ingest refused or failed. Messages never carry a host path."""


@dataclass(frozen=True, slots=True)
class RemoteVideo:
    video_id: str
    title: str
    duration_s: int
    uploader: str | None
    webpage_url: str


def _child_env() -> dict[str, str]:
    env = {n: os.environ[n] for n in _CHILD_ENV_PASSTHROUGH if n in os.environ}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def canonical_video_url(url: str) -> str:
    """Validate a YouTube URL and reduce it to a single canonical video.

    Query parameters are discarded rather than forwarded, which is what strips
    playlist, index and timestamp parameters: a playlist URL cannot turn one
    ingest into hundreds.
    """
    if not isinstance(url, str) or len(url) > 2048:
        raise YouTubeError("invalid URL")

    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise YouTubeError("only https URLs are accepted")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise YouTubeError("only YouTube URLs are accepted")

    if host.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = _query_value(parsed.query, "v")
    elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
        video_id = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    else:
        raise YouTubeError("URL does not name a single video")

    if not _is_video_id(video_id):
        raise YouTubeError("URL does not name a single video")
    return f"https://www.youtube.com/watch?v={video_id}"


def _query_value(query: str, key: str) -> str:
    for part in query.split("&"):
        name, _, value = part.partition("=")
        if name == key:
            return value
    return ""


def _is_video_id(value: str) -> bool:
    return (
        len(value) == 11
        and all(c.isalnum() or c in "-_" for c in value)
    )


def _run_yt_dlp(args: list[str], *, timeout_s: int, cwd: Path) -> str:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        # No user or system config file may add options we did not choose.
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        # Never read cookies from a browser profile.
        "--no-cookies-from-browser",
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout_s,
            env=_child_env(),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise YouTubeError("download exceeded its time limit") from None
    except FileNotFoundError:
        raise YouTubeError(
            "YouTube support is not installed. Install vorquel-watch[youtube]."
        ) from None

    if completed.returncode != 0:
        # yt-dlp's stderr names the local output path and the remote error.
        raise YouTubeError("could not retrieve the video")
    return completed.stdout.decode("utf-8", "replace")


def probe_remote(url: str, *, timeout_s: int = 120) -> RemoteVideo:
    """Read metadata without downloading, so limits are checked first."""
    canonical = canonical_video_url(url)
    raw = _run_yt_dlp(
        ["--dump-single-json", "--skip-download", canonical],
        timeout_s=timeout_s,
        cwd=Path.cwd(),
    )
    try:
        info = json.loads(raw)
    except ValueError:
        raise YouTubeError("could not read video metadata") from None

    if info.get("_type") == "playlist" or "entries" in info:
        raise YouTubeError("playlists are not accepted")

    return RemoteVideo(
        video_id=str(info.get("id") or ""),
        title=str(info.get("title") or "")[:300],
        duration_s=int(info.get("duration") or 0),
        uploader=(str(info["uploader"])[:200] if info.get("uploader") else None),
        webpage_url=canonical,
    )


def download(
    url: str,
    destination: Path,
    *,
    max_duration_s: int = DEFAULT_MAX_DURATION_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[Path, RemoteVideo]:
    """Fetch a single YouTube video into a controlled directory.

    Limits are checked against the announced duration before any bytes are
    fetched, and enforced again by yt-dlp's own size limit during the fetch.
    """
    remote = probe_remote(url)
    if remote.duration_s <= 0:
        raise YouTubeError("could not determine the video duration")
    if remote.duration_s > max_duration_s:
        raise YouTubeError("video exceeds the configured duration limit")

    destination.mkdir(parents=True, exist_ok=True)
    template = str(destination / "%(id)s.%(ext)s")

    _run_yt_dlp(
        [
            "--max-filesize",
            str(int(max_bytes)),
            # Prefer a single already-muxed file: no merging, no post-processing
            # of attacker-supplied streams beyond what the Source Guard will do.
            "--format",
            "best[ext=mp4]/best",
            "--output",
            template,
            remote.webpage_url,
        ],
        timeout_s=timeout_s,
        cwd=destination,
    )

    produced = sorted(destination.glob(f"{remote.video_id}.*"))
    if not produced:
        raise YouTubeError("download produced no file")
    return produced[0], remote
