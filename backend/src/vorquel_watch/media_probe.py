"""Child process that parses untrusted media (SEC-02).

This module is the only place where attacker-controlled bytes meet the FFmpeg
demuxers and decoders, and it runs in its own process so a crash, a hang or a
memory blow-up in that stack cannot take the worker or the MCP server with it.

It reports raw observations only. It makes no policy decision: the container
allowlist, codec allowlist and duration limits are applied by the parent, which
is trusted. Keeping policy out of the process that touches hostile input means
a compromised parse cannot approve itself.

Protocol: argv[1] is the media path. A single JSON object is written to stdout
and the process exits 0, whether the parse succeeded or not. Nothing else is
ever written to stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _codec_name(stream: Any) -> str:
    context = getattr(stream, "codec_context", None)
    name = getattr(context, "name", None)
    if name:
        return str(name)
    codec = getattr(context, "codec", None)
    return str(getattr(codec, "name", "") or "")


def _stream_facts(stream: Any) -> dict[str, Any]:
    context = getattr(stream, "codec_context", None)
    return {
        "type": str(stream.type),
        "codec": _codec_name(stream),
        "width": int(getattr(context, "width", 0) or 0),
        "height": int(getattr(context, "height", 0) or 0),
        "sample_rate": int(getattr(context, "sample_rate", 0) or 0),
        "channels": int(getattr(context, "channels", 0) or 0),
    }


def probe(path: str) -> dict[str, Any]:
    """Return raw container facts. Never raises for bad media."""
    try:
        import av
    except ImportError:
        return {"ok": False, "error": "media_runtime_missing"}

    try:
        with av.open(path, mode="r") as container:
            streams = [_stream_facts(stream) for stream in container.streams]
            return {
                "ok": True,
                "format": container.format.name or "",
                "duration_raw": container.duration,
                "time_base": int(av.time_base),
                "streams": streams,
            }
    except Exception:
        # The exception text embeds the input path and FFmpeg internals. Only a
        # fixed code crosses the process boundary.
        return {"ok": False, "error": "undecodable_media"}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stdout.write(json.dumps({"ok": False, "error": "bad_invocation"}))
        return 0

    sys.stdout.write(json.dumps(probe(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
