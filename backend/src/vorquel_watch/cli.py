from __future__ import annotations

import argparse
import getpass
from importlib.metadata import version as package_version
import json
from pathlib import Path
import shutil
import sys

from vorquel_watch import credentials
from vorquel_watch.config import SECRET_ENV_VAR, Settings, default_data_dir
from vorquel_watch.db import WatchRepository
from vorquel_watch.ingest import ingest_local_file
from vorquel_watch.knowledge import KnowledgeWriter
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.logging_utils import configure_logging
from vorquel_watch.obsidian_export import export_to_obsidian
from vorquel_watch.service import WatchService
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


def _safe_supabase_error(exc: Exception) -> dict[str, str]:
    """Return actionable Supabase diagnostics without echoing sensitive data."""
    code = str(getattr(exc, "code", "") or "")
    message = str(getattr(exc, "message", "") or "").lower()

    if "invalid api key" in message or "invalid jwt" in message or "jwt" in message:
        category = "credential_invalid_or_wrong_project"
    elif "permission denied" in message or code == "42501":
        category = "credential_lacks_required_privileges"
    elif "schema cache" in message or "does not exist" in message or code == "42P01":
        category = "database_schema_mismatch"
    else:
        category = "api_error"

    result = {"category": category}
    if code:
        result["code"] = code[:64]
    return result


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

    secret = getpass.getpass(
        "Supabase API secret key for THIS project "
        "(sb_secret_... or legacy service_role; NOT database password/anon/publishable): "
    ).strip()
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
    configure_logging()
    run_worker(once=args.once, poll_seconds=args.poll_seconds)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Ingest a local file, create/reuse FAST analysis, and process it locally."""
    settings = Settings.from_env()
    source = ingest_local_file(args.path, settings)

    service = WatchService(settings=settings)
    started = service.start_analysis(
        source_id=source["source_id"],
        mode="FAST",
        language_hint=args.language,
    )
    data = started.get("data") or {}
    if "error" in data:
        print(json.dumps(started, ensure_ascii=False, indent=2))
        return 1

    job = data["job"]
    reused = bool(data.get("reused"))

    # A completed compatible result needs no worker. For a newly queued job,
    # process one local job synchronously, then refresh this target job.
    if job.get("status") == "QUEUED":
        configure_logging()
        run_worker(once=True)
        refreshed = service.get_job(job["job_id"])
        refreshed_data = refreshed.get("data") or {}
        if "error" not in refreshed_data:
            job = refreshed_data

    result = {
        "source": source,
        "analysis": {
            "job": job,
            "reused": reused,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if job.get("status") == "SUCCEEDED" else 1




def _brain_writer() -> KnowledgeWriter:
    settings = Settings.from_env()
    return KnowledgeWriter(WatchRepository(settings))



def cmd_brain_propose(args: argparse.Namespace) -> int:
    provenance = []
    if any(
        value is not None
        for value in (
            args.start_ms,
            args.end_ms,
            args.transcript_id,
            args.segment_id,
            args.screen_observation_id,
        )
    ):
        from vorquel_watch.knowledge import KnowledgeProvenance

        provenance.append(
            KnowledgeProvenance(
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                transcript_id=args.transcript_id,
                segment_id=args.segment_id,
                screen_observation_id=args.screen_observation_id,
            )
        )

    try:
        item = _brain_writer().propose(
            source_id=args.source_id,
            knowledge_type=args.knowledge_type,
            domain=args.domain,
            title=args.title,
            summary=args.summary,
            epistemic_status=args.epistemic_status,
            provenance=provenance,
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    print(json.dumps({"candidate": item}, ensure_ascii=False, indent=2))
    return 0

def cmd_brain_list(args: argparse.Namespace) -> int:
    try:
        items = _brain_writer().list_candidates(
            source_id=args.source_id,
            status=args.status,
            limit=args.limit,
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    return 0


def cmd_brain_approve(args: argparse.Namespace) -> int:
    try:
        item = _brain_writer().approve(args.candidate_id, note=args.note)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"knowledge_item": item}, ensure_ascii=False, indent=2))
    return 0


def cmd_brain_reject(args: argparse.Namespace) -> int:
    try:
        item = _brain_writer().reject(args.candidate_id, note=args.note)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"candidate": item}, ensure_ascii=False, indent=2))
    return 0


def cmd_brain_search(args: argparse.Namespace) -> int:
    try:
        items = _brain_writer().search(
            args.query,
            domain=args.domain,
            limit=args.limit,
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    return 0

def cmd_brain_export_obsidian(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    repo = WatchRepository(settings)
    try:
        result = export_to_obsidian(repo, args.vault)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

def cmd_doctor(_: argparse.Namespace) -> int:
    data_dir = default_data_dir()
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info[:2] == (3, 12),
        "data_dir": str(data_dir),
        "credential_backend": "dpapi" if credentials.is_supported() else "unavailable",
        "credential_present": credentials.has_secret(
            data_dir, credentials.SUPABASE_SECRET_NAME
        ),
    }

    storage = LocalStorage(data_dir)
    try:
        storage.ensure()
        disk = shutil.disk_usage(storage.root)
        checks["storage"] = "ok"
        checks["disk_free_bytes"] = disk.free
        checks["disk_free_gib"] = round(disk.free / (1024 ** 3), 2)
    except Exception as exc:
        checks["storage"] = f"failed:{type(exc).__name__}"

    repo = None
    try:
        settings = Settings.from_env()
        repo = WatchRepository(settings)
        repo.healthcheck()
        checks["supabase"] = "ok"
        checks["database_schema"] = repo.schema_healthcheck()
        checks["worker"] = repo.worker_status()

        model_repo, model_revision = settings.whisper_model, None
        try:
            from vorquel_watch.config import resolve_model_pin
            model_repo, model_revision = resolve_model_pin(
                settings.whisper_model, settings.whisper_model_revision
            )
        except Exception as exc:
            checks["model_pin"] = f"failed:{type(exc).__name__}"
        else:
            checks["model_pin"] = {
                "repository": model_repo,
                "revision": model_revision,
                "cache_present": storage.models.exists()
                and any(storage.models.iterdir()),
            }
    except Exception as exc:
        checks["supabase"] = f"failed:{type(exc).__name__}"
        checks["supabase_error"] = _safe_supabase_error(exc)
        checks["database_schema"] = "not_checked"
        checks["worker"] = "not_checked"

    for label, package in (
        ("mcp", "mcp"),
        ("supabase_python", "supabase"),
        ("faster_whisper", "faster-whisper"),
        ("av", "av"),
        ("rapidocr", "rapidocr"),
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

    required_ok = (
        checks.get("python_supported") is True
        and checks.get("credential_present") is True
        and checks.get("storage") == "ok"
        and checks.get("supabase") == "ok"
        and checks.get("mcp") != "missing"
        and checks.get("faster_whisper") != "missing"
        and checks.get("av") != "missing"
        and checks.get("rapidocr") != "missing"
        and checks.get("legacy_plaintext_secret") is False
    )
    checks["ready_for_local_fast"] = required_ok

    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if required_ok else 1


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

    analyze = sub.add_parser(
        "analyze",
        help="Ingest and process a local video/audio file in one command.",
    )
    analyze.add_argument("path")
    analyze.add_argument("--language", default="pt")
    analyze.set_defaults(func=cmd_analyze)

    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    worker.set_defaults(func=cmd_worker)

    brain = sub.add_parser("brain")
    brain_sub = brain.add_subparsers(dest="brain_command", required=True)
    export_obsidian = brain_sub.add_parser(
        "export-obsidian",
        help="Export approved knowledge into the Obsidian vault _generated folder.",
    )
    export_obsidian.add_argument(
        "--vault",
        required=True,
        help="Path to the local Obsidian vault.",
    )
    export_obsidian.set_defaults(func=cmd_brain_export_obsidian)

    propose = brain_sub.add_parser(
        "propose",
        help="Create one PENDING knowledge candidate for explicit human review.",
    )
    propose.add_argument("--source-id", required=True)
    propose.add_argument("--knowledge-type", required=True)
    propose.add_argument("--domain", required=True)
    propose.add_argument("--title", required=True)
    propose.add_argument("--summary", required=True)
    propose.add_argument("--epistemic-status", required=True)
    propose.add_argument("--start-ms", type=int)
    propose.add_argument("--end-ms", type=int)
    propose.add_argument("--transcript-id")
    propose.add_argument("--segment-id")
    propose.add_argument("--screen-observation-id")
    propose.set_defaults(func=cmd_brain_propose)

    list_candidates = brain_sub.add_parser(
        "list",
        help="List reviewed-learning candidates.",
    )
    list_candidates.add_argument("--source-id")
    list_candidates.add_argument(
        "--status",
        choices=["PENDING", "APPROVED", "REJECTED"],
    )
    list_candidates.add_argument("--limit", type=int, default=50)
    list_candidates.set_defaults(func=cmd_brain_list)

    approve = brain_sub.add_parser(
        "approve",
        help="Promote one candidate after explicit human approval.",
    )
    approve.add_argument("candidate_id")
    approve.add_argument("--note")
    approve.set_defaults(func=cmd_brain_approve)

    reject = brain_sub.add_parser(
        "reject",
        help="Reject one candidate after explicit human intent.",
    )
    reject.add_argument("candidate_id")
    reject.add_argument("--note")
    reject.set_defaults(func=cmd_brain_reject)

    search = brain_sub.add_parser(
        "search",
        help="Search approved Vorquel Brain knowledge.",
    )
    search.add_argument("query")
    search.add_argument("--domain")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_brain_search)


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
