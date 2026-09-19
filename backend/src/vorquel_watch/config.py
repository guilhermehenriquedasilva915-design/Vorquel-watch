from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path

from vorquel_watch import credentials


def default_data_dir() -> Path:
    override = os.environ.get("VORQUEL_WATCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "VorquelWatch").resolve()

    return (Path.home() / ".local" / "share" / "vorquel-watch").resolve()


SECRET_ENV_VAR = "VORQUEL_WATCH_SUPABASE_SECRET_KEY"


def _load_config_env(data_dir: Path) -> None:
    """Load non-secret local preferences without overriding process env.

    SEC-03: the secret is never read from this file. It lives in the OS
    credential store. A value left here by an older install is ignored, so a
    stale plaintext secret cannot quietly keep working.
    """
    path = data_dir / "config.env"
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key == SECRET_ENV_VAR or key in os.environ:
            continue
        os.environ[key] = value.strip()


def _resolve_secret(data_dir: Path) -> str:
    """Return the Supabase server credential.

    The environment variable is honoured first so CI and tests can inject a
    value without touching the credential store. Otherwise the secret comes
    from DPAPI. Never falls back to a plaintext file.
    """
    from_env = os.environ.get(SECRET_ENV_VAR, "").strip()
    if from_env:
        return from_env

    if not credentials.is_supported():
        return ""

    try:
        stored = credentials.load_secret(data_dir, credentials.SUPABASE_SECRET_NAME)
    except credentials.CredentialError:
        # The message carries no secret material, but it is not actionable to a
        # caller either; surfacing "not configured" is the useful outcome.
        return ""
    return (stored or "").strip()


# SEC-05: a logical model name is not an identity. faster-whisper resolves
# "small" to the Systran/faster-whisper-small repository, whose contents can
# change upstream at any time, so a name alone lets the weights be swapped
# underneath a completed transcript without anything in the provenance moving.
#
# Each supported model is pinned to an exact upstream commit. Revisions were
# read from the HuggingFace API; both repositories were last modified
# 2023-11-23.
#
# A branch or tag is not accepted as a revision: "main" can move, which is the
# whole failure mode being closed here. Only a full commit hash qualifies.
_REVISION_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")

PINNED_MODEL_REVISIONS: dict[str, tuple[str, str]] = {
    "small": (
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
    ),
    "base": (
        "Systran/faster-whisper-base",
        "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    ),
}


def resolve_model_pin(model: str, revision: str | None) -> tuple[str, str]:
    """Return (repository_id, revision) for a model, or refuse.

    Fails closed. An unrecognised model without an explicit revision is
    rejected rather than downloaded at whatever HEAD happens to be, because a
    silent model change invalidates every transcript produced afterwards.
    """
    name = (model or "").strip()
    if not name:
        raise RuntimeError("no transcription model configured")

    pinned = (revision or "").strip()
    if pinned:
        if not _REVISION_PATTERN.match(pinned):
            raise RuntimeError("model revision must be a 40-character commit hash")
        repository = PINNED_MODEL_REVISIONS.get(name, (name, ""))[0]
        return repository, pinned

    known = PINNED_MODEL_REVISIONS.get(name)
    if known is None:
        raise RuntimeError(
            f"model {name!r} has no pinned revision. Set "
            "VORQUEL_WATCH_WHISPER_MODEL_REVISION to an exact commit hash, or "
            "choose a model with a pin recorded in config."
        )
    return known


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    supabase_url: str
    supabase_secret_key: str
    max_source_bytes: int = 25 * 1024 * 1024 * 1024
    max_duration_ms: int = 8 * 60 * 60 * 1000
    max_video_width: int = 3840
    max_video_height: int = 2160
    max_audio_channels: int = 8
    max_audio_sample_rate: int = 192000
    max_transcript_segments: int = 50000
    max_transcript_text_bytes: int = 64 * 1024 * 1024
    max_export_bytes: int = 128 * 1024 * 1024
    whisper_model: str = "small"
    whisper_model_revision: str | None = None
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    # Screen pipeline (ADR 0002). Enabled for sources that carry video; an
    # audio-only source never pays for it. The interval and threshold are the
    # measured defaults, not guesses: see docs/adr/0002.
    screen_enabled: bool = True
    screen_interval_ms: int = 1500
    screen_change_threshold: float = 0.08
    screen_max_observations: int = 4000
    screen_max_samples: int = 20000
    pipeline_version: str = "watch-alpha/0.1"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = default_data_dir()
        _load_config_env(data_dir)

        url = os.environ.get("VORQUEL_WATCH_SUPABASE_URL", "").strip()
        secret = _resolve_secret(data_dir)
        if not url or not secret:
            raise RuntimeError(
                "Vorquel Watch is not configured. Run 'vorquel-watch configure'."
            )

        return cls(
            data_dir=data_dir,
            supabase_url=url,
            supabase_secret_key=secret,
            max_source_bytes=int(
                os.environ.get(
                    "VORQUEL_WATCH_MAX_SOURCE_BYTES",
                    str(25 * 1024 * 1024 * 1024),
                )
            ),
            max_duration_ms=int(
                os.environ.get(
                    "VORQUEL_WATCH_MAX_DURATION_MS",
                    str(8 * 60 * 60 * 1000),
                )
            ),
            max_video_width=int(os.environ.get("VORQUEL_WATCH_MAX_VIDEO_WIDTH", "3840")),
            max_video_height=int(os.environ.get("VORQUEL_WATCH_MAX_VIDEO_HEIGHT", "2160")),
            max_audio_channels=int(os.environ.get("VORQUEL_WATCH_MAX_AUDIO_CHANNELS", "8")),
            max_audio_sample_rate=int(
                os.environ.get("VORQUEL_WATCH_MAX_AUDIO_SAMPLE_RATE", "192000")
            ),
            max_transcript_segments=int(
                os.environ.get("VORQUEL_WATCH_MAX_TRANSCRIPT_SEGMENTS", "50000")
            ),
            max_transcript_text_bytes=int(
                os.environ.get(
                    "VORQUEL_WATCH_MAX_TRANSCRIPT_TEXT_BYTES",
                    str(64 * 1024 * 1024),
                )
            ),
            max_export_bytes=int(
                os.environ.get(
                    "VORQUEL_WATCH_MAX_EXPORT_BYTES",
                    str(128 * 1024 * 1024),
                )
            ),
            whisper_model=os.environ.get(
                "VORQUEL_WATCH_WHISPER_MODEL", "small"
            ).strip(),
            whisper_device=os.environ.get(
                "VORQUEL_WATCH_WHISPER_DEVICE", "cpu"
            ).strip(),
            whisper_model_revision=(
                os.environ.get("VORQUEL_WATCH_WHISPER_MODEL_REVISION", "").strip()
                or None
            ),
            whisper_compute_type=os.environ.get(
                "VORQUEL_WATCH_WHISPER_COMPUTE_TYPE", "int8"
            ).strip(),
            screen_enabled=os.environ.get(
                "VORQUEL_WATCH_SCREEN_ENABLED", "1"
            ).strip()
            not in {"0", "false", "False", "no"},
            screen_interval_ms=int(
                os.environ.get("VORQUEL_WATCH_SCREEN_INTERVAL_MS", "1500")
            ),
            screen_change_threshold=float(
                os.environ.get("VORQUEL_WATCH_SCREEN_CHANGE_THRESHOLD", "0.08")
            ),
            screen_max_observations=int(
                os.environ.get("VORQUEL_WATCH_SCREEN_MAX_OBSERVATIONS", "4000")
            ),
            screen_max_samples=int(
                os.environ.get("VORQUEL_WATCH_SCREEN_MAX_SAMPLES", "20000")
            ),
            pipeline_version=os.environ.get(
                "VORQUEL_WATCH_PIPELINE_VERSION", "watch-alpha/0.1"
            ).strip(),
        )
