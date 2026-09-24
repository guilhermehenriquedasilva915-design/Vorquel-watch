"""Remote processing API (ADR 0009).

One job: let a notebook hand a workshop recording to the VPS, get identifiers
back, disconnect, and ask about progress later. The VPS keeps processing without
it.

This is a thin transport shell. It creates no pipeline of its own: uploads land
in the same content-addressed store, through the same out-of-process probe and
the same allowlists as a local ingest, and are processed by the same worker
already running on the host. Arriving over the network buys a source no extra
trust, so media and media-derived text stay UNTRUSTED with
instruction_authority NONE.

What this surface deliberately does not offer, because the absence is the
boundary:

- no filesystem path in, and none out
- no URL to fetch, no shell, no database passthrough
- no media bytes served back
- no Supabase credential reachable by a client

Bound to loopback by default and reached over an SSH tunnel, so the VPS needs no
new public port. The bearer token is required regardless: loopback is not
authentication.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from vorquel_watch import remote_auth
from vorquel_watch.config import Settings
from vorquel_watch.envelope import envelope, safe_error
from vorquel_watch.ingest import ingest_uploaded_file
from vorquel_watch.logging_utils import configure_logging, log_event
from vorquel_watch.service import WatchService


LOG = logging.getLogger("vorquel_watch.remote_api")

_DIGEST_HEADER = "x-vorquel-content-sha256"
_FILENAME_HEADER = "x-vorquel-filename"

# Routes reachable without a token. Only liveness, and it returns a constant:
# an unauthenticated caller learns that something answers, and nothing else -
# no version, no configuration, no counts.
_PUBLIC_PATHS = frozenset({"/v1/health"})

# Keep some room so a completed upload cannot fill the disk to zero and strand
# the worker, which needs scratch space to extract audio and frames.
_FREE_SPACE_MARGIN_BYTES = 2 * 1024 * 1024 * 1024

_HTTP_FOR_ERROR_CODE = {
    "INVALID_ARGUMENT": 400,
    "INVALID_SOURCE": 400,
    "MODE_NOT_AVAILABLE": 400,
    "NOT_FOUND": 404,
}


def _error(tool: str, code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(safe_error(tool, code, message), status_code=status)


def _service_response(tool: str, payload: dict[str, Any]) -> JSONResponse:
    """Map a WatchService envelope onto an HTTP status.

    The envelope is passed through untouched: it already carries the security
    block and is already free of host paths, so rewriting it here could only
    make it less accurate.
    """
    data = payload.get("data")
    if isinstance(data, dict) and data.get("ok") is False:
        code = str((data.get("error") or {}).get("code") or "INVALID_ARGUMENT")
        return JSONResponse(payload, status_code=_HTTP_FOR_ERROR_CODE.get(code, 400))
    return JSONResponse(payload, status_code=200)


def _incoming_dir(data_dir: Path) -> Path:
    """Where a partial upload lives before it is validated.

    Deliberately inside data_dir and outside the content-addressed tree: an
    unverified file must never sit where a digest-addressed object is expected.
    """
    target = data_dir / "incoming"
    target.mkdir(parents=True, exist_ok=True)
    return target


def purge_stale_uploads(data_dir: Path) -> int:
    """Delete leftover partial uploads. Returns how many were removed.

    A process killed mid-upload leaves a .part file that no request will ever
    finish. Nothing references it, so it is dead bytes on a disk whose capacity
    is the scarce resource.
    """
    removed = 0
    try:
        for stale in _incoming_dir(data_dir).glob("*.part"):
            try:
                stale.unlink()
                removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


class _UploadTooLarge(Exception):
    pass


class RemoteApi:
    def __init__(
        self,
        *,
        settings: Settings,
        service: WatchService,
        token: str,
        ingest: Callable[..., dict[str, Any]] = ingest_uploaded_file,
    ) -> None:
        self.settings = settings
        self.service = service
        self.token = token
        self._ingest = ingest

    # -- authentication ---------------------------------------------------
    def authorized(self, request: Request) -> bool:
        return remote_auth.is_authorized(
            request.headers.get("authorization"), self.token
        )

    # -- routes -----------------------------------------------------------
    async def health(self, request: Request) -> JSONResponse:
        return JSONResponse({"ok": True}, status_code=200)

    async def source_by_digest(self, request: Request) -> JSONResponse:
        """Tell a client whether this content is already held.

        Lets a notebook skip re-sending gigabytes it already sent. The answer is
        advisory: an actual upload is still hashed and deduplicated on the
        digest the server computed, never on one a client asserted.
        """
        digest = (request.path_params.get("content_sha256") or "").lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return _error(
                "remote_source_by_digest",
                "INVALID_ARGUMENT",
                "content_sha256 must be a 64-character hex digest.",
                400,
            )

        existing = await run_in_threadpool(
            self.service.repo.find_source_by_hash, digest
        )
        if not existing:
            return _error(
                "remote_source_by_digest",
                "NOT_FOUND",
                "No source is held for that digest.",
                404,
            )
        return JSONResponse(
            envelope(
                "remote_source_by_digest",
                {
                    "source_id": existing["source_id"],
                    "duration_ms": existing.get("duration_ms"),
                    "source_kind": existing.get("source_kind"),
                    "held": True,
                },
                contains_untrusted_content=False,
            ),
            status_code=200,
        )

    async def upload(self, request: Request) -> JSONResponse:
        limit = int(self.settings.max_source_bytes)

        declared_length = request.headers.get("content-length")
        if declared_length is None:
            return _error(
                "remote_upload",
                "LENGTH_REQUIRED",
                "Content-Length is required so the size limit can be applied "
                "before the body is read.",
                411,
            )
        try:
            declared_bytes = int(declared_length)
        except ValueError:
            return _error(
                "remote_upload",
                "INVALID_ARGUMENT",
                "Content-Length is not a number.",
                400,
            )
        if declared_bytes <= 0:
            return _error("remote_upload", "INVALID_ARGUMENT", "Upload is empty.", 400)
        # Refuse an oversized upload before a single byte is transferred.
        if declared_bytes > limit:
            return _error(
                "remote_upload",
                "SOURCE_TOO_LARGE",
                "Upload exceeds the configured byte limit.",
                413,
            )

        declared_digest = (request.headers.get(_DIGEST_HEADER) or "").strip().lower()
        if declared_digest and (
            len(declared_digest) != 64
            or any(c not in "0123456789abcdef" for c in declared_digest)
        ):
            return _error(
                "remote_upload",
                "INVALID_ARGUMENT",
                "The declared content digest must be a 64-character hex digest.",
                400,
            )

        if not self._has_room(declared_bytes):
            return _error(
                "remote_upload",
                "INSUFFICIENT_STORAGE",
                "The host does not have enough free space for this upload.",
                507,
            )

        incoming = _incoming_dir(self.settings.data_dir)
        temp = incoming / f"{uuid.uuid4().hex}.part"

        try:
            received, computed = await self._receive(request, temp, limit)
        except _UploadTooLarge:
            self._discard(temp)
            log_event(
                LOG, logging.WARNING, "remote_upload_rejected", reason="TOO_LARGE"
            )
            return _error(
                "remote_upload",
                "SOURCE_TOO_LARGE",
                "Upload exceeds the configured byte limit.",
                413,
            )
        except OSError:
            self._discard(temp)
            log_event(LOG, logging.ERROR, "remote_upload_failed", reason="WRITE_FAILED")
            return _error(
                "remote_upload",
                "UPLOAD_FAILED",
                "The upload could not be stored on the host.",
                500,
            )

        try:
            if received != declared_bytes:
                # A truncated or over-long body is a failed transfer, not media
                # to validate. Rejecting here keeps a partial recording from
                # becoming a source whose transcript silently stops early.
                log_event(
                    LOG,
                    logging.WARNING,
                    "remote_upload_rejected",
                    reason="LENGTH_MISMATCH",
                    received_bytes=received,
                )
                return _error(
                    "remote_upload",
                    "LENGTH_MISMATCH",
                    "The uploaded byte count did not match Content-Length.",
                    400,
                )

            if declared_digest and computed != declared_digest:
                log_event(
                    LOG,
                    logging.WARNING,
                    "remote_upload_rejected",
                    reason="DIGEST_MISMATCH",
                    received_bytes=received,
                )
                return _error(
                    "remote_upload",
                    "DIGEST_MISMATCH",
                    "The uploaded content did not match the declared digest.",
                    400,
                )

            return await self._register_and_queue(
                temp,
                original_filename=request.headers.get(_FILENAME_HEADER),
                received=received,
                computed=computed,
            )
        finally:
            # The validated bytes now live in the content-addressed store, so
            # the partial copy is redundant either way.
            self._discard(temp)

    async def _register_and_queue(
        self,
        temp: Path,
        *,
        original_filename: str | None,
        received: int,
        computed: str,
    ) -> JSONResponse:
        """Validate, store, register and queue. Blocking work runs off the loop."""
        try:
            ingested = await run_in_threadpool(
                self._ingest,
                temp,
                self.settings,
                original_filename=original_filename,
            )
        except ValueError as exc:
            # Policy rejection from the guard: unsupported container, blocked
            # codec, over a limit. The guard's message is already path-free and
            # says nothing about the host.
            log_event(
                LOG,
                logging.WARNING,
                "remote_upload_rejected",
                reason="MEDIA_REJECTED",
                received_bytes=received,
                digest_prefix=computed[:12],
            )
            return _error("remote_upload", "MEDIA_REJECTED", str(exc), 415)
        except Exception as exc:
            log_event(
                LOG,
                logging.ERROR,
                "remote_upload_failed",
                reason="INGEST_FAILED",
                exception_type=type(exc).__name__,
            )
            return _error(
                "remote_upload",
                "INGEST_FAILED",
                "The upload could not be ingested.",
                500,
            )

        source_id = ingested["source_id"]
        started = await run_in_threadpool(self.service.start_analysis, source_id)
        started_data = started.get("data")
        if isinstance(started_data, dict) and started_data.get("ok") is False:
            # The source is registered and safe; only queueing failed. Report
            # that honestly instead of implying nothing happened.
            return JSONResponse(started, status_code=400)

        job = (started_data or {}).get("job") or {}
        log_event(
            LOG,
            logging.INFO,
            "remote_upload_accepted",
            source_id=source_id,
            job_id=job.get("job_id"),
            reused_source=bool(ingested.get("reused")),
            reused_job=bool((started_data or {}).get("reused")),
            received_bytes=received,
            digest_prefix=computed[:12],
        )
        return JSONResponse(
            envelope(
                "remote_upload",
                {
                    "source_id": source_id,
                    "job_id": job.get("job_id"),
                    "job_status": job.get("status"),
                    "duration_ms": ingested.get("duration_ms"),
                    "source_kind": ingested.get("source_kind"),
                    "reused_source": bool(ingested.get("reused")),
                    "reused_job": bool((started_data or {}).get("reused")),
                    "received_bytes": received,
                    "content_sha256": computed,
                },
                contains_untrusted_content=False,
            ),
            status_code=202,
        )

    async def get_job(self, request: Request) -> JSONResponse:
        job_id = request.path_params.get("job_id") or ""
        payload = await run_in_threadpool(self.service.get_job, job_id)
        return _service_response("remote_get_job", payload)

    async def list_jobs(self, request: Request) -> JSONResponse:
        params = request.query_params
        try:
            limit = int(params.get("limit") or 20)
        except ValueError:
            return _error(
                "remote_list_jobs", "INVALID_ARGUMENT", "limit is not a number.", 400
            )
        status = params.get("status")
        cursor = params.get("cursor")
        try:
            listing = await run_in_threadpool(
                lambda: self.service.repo.list_jobs(
                    limit=limit, cursor=cursor, status=status
                )
            )
        except ValueError as exc:
            return _error("remote_list_jobs", "INVALID_ARGUMENT", str(exc), 400)
        return JSONResponse(
            envelope("remote_list_jobs", listing, contains_untrusted_content=False),
            status_code=200,
        )

    async def cancel_job(self, request: Request) -> JSONResponse:
        job_id = request.path_params.get("job_id") or ""
        payload = await run_in_threadpool(self.service.cancel_job, job_id)
        return _service_response("remote_cancel_job", payload)

    # -- helpers ----------------------------------------------------------
    def _has_room(self, needed_bytes: int) -> bool:
        try:
            free = shutil.disk_usage(self.settings.data_dir).free
        except OSError:
            # Unable to measure is not permission to fill the disk.
            return False
        return free >= needed_bytes + _FREE_SPACE_MARGIN_BYTES

    async def _receive(
        self, request: Request, temp: Path, limit: int
    ) -> tuple[int, str]:
        """Stream the body to disk, hashing as it arrives.

        Content-Length is a claim, not a guarantee, so the cap is enforced again
        on the bytes actually received. Nothing is buffered whole in memory: a
        multi-gigabyte upload costs one chunk of RAM.
        """
        digest = hashlib.sha256()
        received = 0
        with temp.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > limit:
                    raise _UploadTooLarge()
                digest.update(chunk)
                handle.write(chunk)
        return received, digest.hexdigest()

    def _discard(self, temp: Path) -> None:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def create_app(
    *,
    settings: Settings | None = None,
    service: WatchService | None = None,
    token: str | None = None,
    ingest: Callable[..., dict[str, Any]] = ingest_uploaded_file,
) -> Starlette:
    """Build the ASGI app.

    Dependencies are injectable so the trust boundaries can be tested without a
    database, a network listener or a real credential.
    """
    resolved_settings = settings or Settings.from_env()
    resolved_service = service or WatchService(settings=resolved_settings)
    resolved_token = token if token is not None else remote_auth.load_server_token()

    api = RemoteApi(
        settings=resolved_settings,
        service=resolved_service,
        token=resolved_token,
        ingest=ingest,
    )

    async def guard(request: Request, call_next):
        if request.url.path not in _PUBLIC_PATHS and not api.authorized(request):
            log_event(
                LOG,
                logging.WARNING,
                "remote_request_unauthorized",
                method=request.method,
            )
            return _error(
                "remote_api",
                "UNAUTHORIZED",
                "Authentication is required.",
                401,
            )
        return await call_next(request)

    routes = [
        Route("/v1/health", api.health, methods=["GET"]),
        Route("/v1/uploads", api.upload, methods=["POST"]),
        Route(
            "/v1/sources/by-digest/{content_sha256}",
            api.source_by_digest,
            methods=["GET"],
        ),
        Route("/v1/jobs", api.list_jobs, methods=["GET"]),
        Route("/v1/jobs/{job_id}", api.get_job, methods=["GET"]),
        Route("/v1/jobs/{job_id}/cancel", api.cancel_job, methods=["POST"]),
    ]

    app = Starlette(
        routes=routes,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=guard)],
    )
    removed = purge_stale_uploads(resolved_settings.data_dir)
    if removed:
        log_event(LOG, logging.INFO, "stale_uploads_purged", removed=removed)
    return app


def main() -> None:
    """Run the API. Binds loopback unless told otherwise."""
    import uvicorn

    configure_logging()
    host = os.environ.get("VORQUEL_WATCH_REMOTE_HOST", "127.0.0.1").strip()
    port = int(os.environ.get("VORQUEL_WATCH_REMOTE_PORT", "8787"))
    # proxy_headers off: nothing is in front of this, so an X-Forwarded-For from
    # a client must not be believed.
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        proxy_headers=False,
        server_header=False,
        log_config=None,
    )
