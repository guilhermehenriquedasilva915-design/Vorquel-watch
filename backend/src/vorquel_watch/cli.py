from __future__ import annotations

import argparse
import getpass
from importlib.metadata import version as package_version
import json
from pathlib import Path
import sys

from vorquel_watch import credentials
from vorquel_watch.config import SECRET_ENV_VAR, Settings, default_data_dir
from vorquel_watch.db import WatchRepository
from vorquel_watch.ingest import ingest_local_file
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.worker import run_worker


CONFIG_FILE_NAME = "config.env"


def _write_preferences(url: str) -> Path:
    """Write non-secret local preferences.

    SEC-03: the Supabase credential is deliberately absent from this file. It
    is stored encrypted by the OS credential store.
    """
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / CONFIG_FILE_NAME
    path.write_text(
        "# Vorquel Watch local preferences\n"
        "# The Supabase secret is NOT stored here. See 'vorquel-watch credential'.\n"
        f"VORQUEL_WATCH_SUPABASE_URL={url.strip()}\n"
        "VORQUEL_WATCH_WHISPER_MODEL=small\n"
        "VORQUEL_WATCH_WHISPER_DEVICE=cpu\n"
        "VORQUEL_WATCH_WHISPER_COMPUTE_TYPE=int8\n",
        encoding="utf-8",
    )
    return path


def purge_legacy_secret(path: Path) -> bool:
    """Strip a plaintext secret left by an older install. Returns whether one was found."""
    if not path.is_file():
        return False

    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.strip().startswith(f"{SECRET_ENV_VAR}=")]
    if len(kept) == len(lines):
        return False

    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


def cmd_configure(args: argparse.Namespace) -> int:
    url = (args.url or input("Supabase project URL: ")).strip()
    if not url.startswith("https://"):
        print("Invalid URL.", file=sys.stderr)
        return 2

    if not credentials.is_supported():
        print(
            "OS-backed credential storage is unavailable on this platform. "
            "Refusing to store the credential in plaintext.",
            file=sys.stderr,
        )
        return 2

    secret = getpass.getpass("Supabase secret/service key (input hidden): ").strip()
    if not secret:
        print("Empty secret.", file=sys.stderr)
        return 2

    data_dir = default_data_dir()
    try:
        credentials.store_secret(data_dir, credentials.SUPABASE_SECRET_NAME, secret)
    except credentials.CredentialError as exc:
        print(f"Could not store the credential: {exc}", file=sys.stderr)
        return 1
    finally:
        del secret

    path = _write_preferences(url)
    if purge_legacy_secret(path):
        print("Removed a plaintext secret left by a previous install.")

    print(f"Preferences saved at: {path}")
    print("The credential was encrypted with DPAPI and is not in any file you can read.")
    return 0


def cmd_credential(args: argparse.Namespace) -> int:
    data_dir = default_data_dir()

    if args.action == "status":
        present = credentials.has_secret(data_dir, credentials.SUPABASE_SECRET_NAME)
        print(
            json.dumps(
                {
                    "backend": "dpapi" if credentials.is_supported() else "unavailable",
                    "credential_present": present,
                },
                indent=2,
            )
        )
        return 0 if present else 1

    if args.action == "remove":
        removed = credentials.delete_secret(
            data_dir, credentials.SUPABASE_SECRET_NAME
        )
        print("Credential removed." if removed else "No credential was stored.")
        return 0

    # set / rotate share the same path: store overwrites any previous value.
    if not credentials.is_supported():
        print(
            "OS-backed credential storage is unavailable on this platform.",
            file=sys.stderr,
        )
        return 2

    secret = getpass.getpass("Supabase secret/service key (input hidden): ").strip()
    if not secret:
        print("Empty secret.", file=sys.stderr)
        return 2
    try:
        credentials.store_secret(data_dir, credentials.SUPABASE_SECRET_NAME, secret)
    except credentials.CredentialError as exc:
        print(f"Could not store the credential: {exc}", file=sys.stderr)
        return 1
    finally:
        del secret

    print("Credential stored.")
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
    data_dir = default_data_dir()
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "data_dir": str(data_dir),
        "credential_backend": "dpapi" if credentials.is_supported() else "unavailable",
        "credential_present": credentials.has_secret(
            data_dir, credentials.SUPABASE_SECRET_NAME
        ),
    }

    try:
        settings = Settings.from_env()
        LocalStorage(settings.data_dir).ensure()
        repo = WatchRepository(settings)
        repo.healthcheck()
        checks["supabase"] = "ok"
    except Exception as exc:
        checks["supabase"] = f"failed:{type(exc).__name__}"

    for label, package in (
        ("mcp", "mcp"),
        ("supabase_python", "supabase"),
        ("faster_whisper", "faster-whisper"),
        ("av", "av"),
    ):
        try:
            checks[label] = package_version(package)
        except Exception:
            checks[label] = "missing"

    legacy = data_dir / CONFIG_FILE_NAME
    checks["legacy_plaintext_secret"] = bool(
        legacy.is_file()
        and any(
            line.strip().startswith(f"{SECRET_ENV_VAR}=")
            for line in legacy.read_text(encoding="utf-8").splitlines()
        )
    )

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

    credential = sub.add_parser("credential")
    credential.add_argument("action", choices=["set", "rotate", "remove", "status"])
    credential.set_defaults(func=cmd_credential)

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
