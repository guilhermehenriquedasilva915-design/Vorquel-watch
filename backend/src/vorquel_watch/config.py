from __future__ import annotations

from dataclasses import dataclass
import os
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


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    supabase_url: str
    supabase_secret_key: str
    max_source_bytes: int = 25 * 1024 * 1024 * 1024
    max_duration_ms: int = 8 * 60 * 60 * 1000
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
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
            whisper_model=os.environ.get(
                "VORQUEL_WATCH_WHISPER_MODEL", "small"
            ).strip(),
            whisper_device=os.environ.get(
                "VORQUEL_WATCH_WHISPER_DEVICE", "cpu"
            ).strip(),
            whisper_compute_type=os.environ.get(
                "VORQUEL_WATCH_WHISPER_COMPUTE_TYPE", "int8"
            ).strip(),
            pipeline_version=os.environ.get(
                "VORQUEL_WATCH_PIPELINE_VERSION", "watch-alpha/0.1"
            ).strip(),
        )
