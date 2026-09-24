"""Client for the remote processing API (ADR 0009).

Runs on the notebook. Holds exactly one secret, the API token, and never the
Supabase credential: a stolen notebook cannot reach the database, only ask this
one host to process media and report job state.

The upload streams from disk, so a multi-gigabyte workshop recording never sits
in memory. The file is hashed before it is sent, which buys two things: the
server can reject a corrupted transfer instead of transcribing it, and an
already-held recording can be skipped without moving its bytes at all.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

import httpx

from vorquel_watch import credentials, remote_auth
from vorquel_watch.local_storage import sha256_file


URL_ENV_VAR = "VORQUEL_WATCH_REMOTE_URL"

DEFAULT_BASE_URL = "http://127.0.0.1:8787"

# Uploading a long recording over a home connection is slow but not stalled.
# Only the connect phase gets a short timeout; the write phase gets none, so a
# genuine multi-hour upload is not killed by a stopwatch.
_CONNECT_TIMEOUT_SECONDS = 15.0
_READ_TIMEOUT_SECONDS = 120.0

_STREAM_CHUNK_BYTES = 1024 * 1024


class RemoteClientError(RuntimeError):
    """A remote call could not be completed.

    The message is safe to print: it carries no token and no server-side path.
    """


def resolve_base_url(explicit: str | None = None) -> str:
    return (explicit or os.environ.get(URL_ENV_VAR) or DEFAULT_BASE_URL).rstrip("/")


def resolve_token(data_dir: Path, explicit: str | None = None) -> str:
    """Find the API token for this client.

    Order: an explicit value, then the environment, then the OS-backed
    credential store. There is no plaintext-file fallback, for the same reason
    the Supabase secret has none.
    """
    direct = (explicit or "").strip()
    if direct:
        return direct

    from_env = (os.environ.get(remote_auth.TOKEN_ENV_VAR) or "").strip()
    if from_env:
        return from_env

    if credentials.is_supported():
        try:
            stored = credentials.load_secret(
                data_dir, remote_auth.CLIENT_TOKEN_SECRET_NAME
            )
        except credentials.CredentialError:
            stored = None
        if stored and stored.strip():
            return stored.strip()

    raise RemoteClientError(
        "No remote API token is configured. Run "
        "'vorquel-watch remote configure' or set "
        f"{remote_auth.TOKEN_ENV_VAR}."
    )


def _iter_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                return
            yield chunk


class RemoteClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        content: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        merged = self._headers()
        if headers:
            merged.update(headers)

        url = f"{self.base_url}{path}"
        try:
            if self._client is not None:
                response = self._client.request(
                    method, url, content=content, headers=merged, params=params
                )
            else:
                with httpx.Client(
                    timeout=timeout
                    or httpx.Timeout(
                        _READ_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS
                    )
                ) as client:
                    response = client.request(
                        method, url, content=content, headers=merged, params=params
                    )
        except httpx.HTTPError as exc:
            # Only the exception class is surfaced. httpx messages can embed the
            # full URL, and the URL is operator configuration.
            raise RemoteClientError(
                f"The remote host could not be reached ({type(exc).__name__})."
            ) from None

        if response.status_code == 401:
            raise RemoteClientError(
                "The remote host rejected the API token."
            )

        try:
            payload = response.json()
        except ValueError:
            raise RemoteClientError(
                f"The remote host returned a non-JSON response "
                f"(HTTP {response.status_code})."
            ) from None

        if not isinstance(payload, dict):
            raise RemoteClientError("The remote host returned an unexpected response.")
        payload["_http_status"] = response.status_code
        return payload

    # -- operations -------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def source_by_digest(self, content_sha256: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sources/by-digest/{content_sha256}")

    def upload(self, path: Path, *, skip_if_held: bool = True) -> dict[str, Any]:
        """Send one media file for processing.

        Returns the server envelope. When the content is already held and
        skip_if_held is set, no bytes are transferred and the existing
        source is reported instead.
        """
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise RemoteClientError("The upload target is not a regular file.")

        byte_size = resolved.stat().st_size
        if byte_size <= 0:
            raise RemoteClientError("The upload target is empty.")

        digest = sha256_file(resolved)

        if skip_if_held:
            held = self.source_by_digest(digest)
            if held.get("_http_status") == 200:
                data = dict(held.get("data") or {})
                data["skipped_upload"] = True
                held["data"] = data
                return held

        headers = {
            "content-type": "application/octet-stream",
            "content-length": str(byte_size),
            "x-vorquel-content-sha256": digest,
            # The server treats this as untrusted metadata and files the object
            # under its digest, never under this name.
            "x-vorquel-filename": _header_safe(resolved.name),
        }
        return self._request(
            "POST",
            "/v1/uploads",
            content=_iter_file(resolved),
            headers=headers,
            timeout=httpx.Timeout(
                None, connect=_CONNECT_TIMEOUT_SECONDS, read=_READ_TIMEOUT_SECONDS
            ),
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def list_jobs(
        self, *, limit: int = 20, status: str | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/jobs", params=params)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")


def _header_safe(value: str) -> str:
    """Reduce a filename to something that cannot forge a header.

    A header value carrying CR or LF can inject another header. The server also
    cleans what it stores, but a client should not emit a malformed request in
    the first place.
    """
    cleaned = "".join(
        ch for ch in value if ch.isprintable() and ch not in "\r\n"
    ).strip()
    return cleaned[:200] or "media"


def build_client(
    data_dir: Path,
    *,
    base_url: str | None = None,
    token: str | None = None,
) -> RemoteClient:
    return RemoteClient(
        base_url=resolve_base_url(base_url),
        token=resolve_token(data_dir, token),
    )
