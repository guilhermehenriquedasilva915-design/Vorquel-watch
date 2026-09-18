from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("VORQUEL_WATCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "VorquelWatch").resolve()

    return (Path.home() / ".local" / "share" / "vorquel-watch").resolve()


def _load_config_env(data_dir: Path) -> None:
    """Load the local control-plane env file without overriding process env."""
    path = data_dir / "config.env"
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()


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
        secret = os.environ.get("VORQUEL_WATCH_SUPABASE_SECRET_KEY", "").strip()
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
