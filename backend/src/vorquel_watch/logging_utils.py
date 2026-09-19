"""Structured, redacted application logging.

The Watch handles media-derived text and backend credentials. Logs are an
operational surface, not an evidence store, so they deliberately carry opaque
IDs, bounded metrics and fixed error codes rather than arbitrary exception
messages, transcripts, host paths or URLs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


_FORBIDDEN_FIELD_PARTS = (
    "secret",
    "token",
    "cookie",
    "credential",
    "password",
    "authorization",
    "path",
    "home",
    "url",
    "transcript",
    "text",
    "content",
    "prompt",
)

_MAX_STRING = 256


def _safe_key(key: str) -> bool:
    normalized = key.lower()
    return not any(part in normalized for part in _FORBIDDEN_FIELD_PARTS)


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # IDs, stages, error codes and version strings are the intended string
        # payloads. Bound them so a caller cannot turn logging into a transcript
        # sink accidentally.
        return value[:_MAX_STRING]
    return str(type(value).__name__)


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Return only operationally safe, bounded fields.

    Sensitive key names are dropped rather than heuristically redacted. This is
    intentionally conservative: logs do not need paths, URLs or transcript
    content to explain what the worker did.
    """
    return {
        key: _safe_value(value)
        for key, value in fields.items()
        if _safe_key(key)
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "watch_event", record.getMessage()),
        }
        payload.update(safe_fields(getattr(record, "watch_fields", {})))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """Emit one structured event without exception text or traceback."""
    logger.log(
        level,
        event,
        extra={
            "watch_event": event,
            "watch_fields": safe_fields(fields),
        },
    )


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
