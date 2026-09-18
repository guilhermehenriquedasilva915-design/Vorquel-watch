from __future__ import annotations

import argparse
import getpass
from importlib.metadata import version as package_version
import json
import os
from pathlib import Path
import stat
import sys

from vorquel_watch.config import Settings, default_data_dir
from vorquel_watch.db import WatchRepository
from vorquel_watch.ingest import ingest_local_file
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.worker import run_worker


def _write_config(url: str, secret: str) -> Path:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "config.env"
    content = (
        "# Vorquel Watch local control-plane configuration\n"
        f"VORQUEL_WATCH_SUPABASE_URL={url.strip()}\n"
        f"VORQUEL_WATCH_SUPABASE_SECRET_KEY={secret.strip()}\n"
        "VORQUEL_WATCH_WHISPER_MODEL=small\n"
        "VORQUEL_WATCH_WHISPER_DEVICE=cpu\n"
        "VORQUEL_WATCH_WHISPER_COMPUTE_TYPE=int8\n"
    )
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def cmd_configure(args: argparse.Namespace) -> int:
    url = args.url or input("Supabase project URL: ").strip()
    secret = getpass.getpass("Supabase secret/service key (input hidden): ").strip()
    if not url.startswith("https://") or not secret:
        print("Invalid URL or empty secret.", file=sys.stderr)
        return 2
    path = _write_config(url, secret)
    print(f"Configuration saved locally at: {path}")
    print("The secret was not written to the Git repository.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    result = ingest_local_file(args.path, settings)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    run_worker(once=args.once, poll_seconds=args.poll_seconds)
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "data_dir": str(default_data_dir()),
    }
    try:
        settings = Settings.from_env()
        LocalStorage(settings.data_dir).ensure()
        repo = WatchRepository(settings)
        repo.healthcheck()
        checks["supabase"] = "ok"
    except Exception as exc:
        checks["supabase"] = f"failed:{type(exc).__name__}"

    try:
        checks["mcp"] = package_version("mcp")
    except Exception:
        checks["mcp"] = "missing"

    try:
        checks["supabase_python"] = package_version("supabase")
    except Exception:
        checks["supabase_python"] = "missing"

    try:
        checks["faster_whisper"] = package_version("faster-whisper")
    except Exception:
        checks["faster_whisper"] = "missing"

    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks.get("supabase") == "ok" else 1


def cmd_print_claude_config(_: argparse.Namespace) -> int:
    payload = {
        "mcpServers": {
            "vorquel-watch": {
                "command": sys.executable,
                "args": ["-m", "vorquel_watch.mcp_server"],
            }
        }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vorquel-watch")
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure")
    configure.add_argument("--url")
    configure.set_defaults(func=cmd_configure)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("path")
    ingest.set_defaults(func=cmd_ingest)

    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    worker.set_defaults(func=cmd_worker)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    claude = sub.add_parser("print-claude-config")
    claude.set_defaults(func=cmd_print_claude_config)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
