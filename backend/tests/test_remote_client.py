"""The notebook-side client.

Two properties matter most here. The client must stream from disk rather than
load a workshop recording into memory, and it must never widen the secret
surface: it carries the API token and nothing else, and it does not print or
embed either the token or the server URL in the errors it raises.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from vorquel_watch import remote_auth
from vorquel_watch.remote_client import (
    DEFAULT_BASE_URL,
    RemoteClient,
    RemoteClientError,
    resolve_base_url,
    resolve_token,
)


TOKEN = "k" * 48
CONTENT = b"pretend-media" * 64
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def _envelope(data: dict) -> dict:
    return {"control": {"tool": "t"}, "security": {}, "data": data}


class _Recorder:
    """Captures what the client actually put on the wire."""

    def __init__(self, responder) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self._responder(request)


def _client(responder) -> tuple[RemoteClient, _Recorder]:
    recorder = _Recorder(responder)
    transport = httpx.MockTransport(recorder.handler)
    return (
        RemoteClient(
            base_url="http://127.0.0.1:8787",
            token=TOKEN,
            client=httpx.Client(transport=transport),
        ),
        recorder,
    )


class ConfigurationTests(unittest.TestCase):
    def test_base_url_defaults_to_tunnelled_loopback(self) -> None:
        """The default must not reach out to a public address."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_base_url(), DEFAULT_BASE_URL)
        self.assertTrue(DEFAULT_BASE_URL.startswith("http://127.0.0.1"))

    def test_explicit_url_overrides_environment(self) -> None:
        with mock.patch.dict(
            os.environ, {"VORQUEL_WATCH_REMOTE_URL": "http://env.invalid"}, clear=True
        ):
            self.assertEqual(
                resolve_base_url("http://explicit.invalid"), "http://explicit.invalid"
            )

    def test_trailing_slash_is_normalised(self) -> None:
        self.assertEqual(
            resolve_base_url("http://host.invalid/"), "http://host.invalid"
        )

    def test_missing_token_is_a_clear_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch(
                    "vorquel_watch.credentials.is_supported", return_value=False
                ):
                    with self.assertRaises(RemoteClientError) as caught:
                        resolve_token(Path(tmp))
        self.assertIn(remote_auth.TOKEN_ENV_VAR, str(caught.exception))

    def test_token_comes_from_the_environment_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {remote_auth.TOKEN_ENV_VAR: TOKEN}, clear=True
            ):
                self.assertEqual(resolve_token(Path(tmp)), TOKEN)


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.media = Path(self._tmp.name) / "workshop.mp4"
        self.media.write_bytes(CONTENT)

    def test_upload_sends_token_digest_and_length(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sources/by-digest/"):
                return httpx.Response(404, json=_envelope({"ok": False, "error": {}}))
            return httpx.Response(
                202, json=_envelope({"source_id": "src_x", "job_id": "job_y"})
            )

        client, recorder = _client(responder)
        result = client.upload(self.media)

        upload = recorder.requests[-1]
        self.assertEqual(upload.method, "POST")
        self.assertEqual(upload.url.path, "/v1/uploads")
        self.assertEqual(upload.headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(upload.headers["x-vorquel-content-sha256"], DIGEST)
        self.assertEqual(upload.headers["content-length"], str(len(CONTENT)))
        self.assertEqual(upload.content, CONTENT)
        self.assertEqual(result["data"]["job_id"], "job_y")

    def test_already_held_content_is_not_uploaded(self) -> None:
        """The whole point of the pre-flight check: do not resend gigabytes."""

        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sources/by-digest/"):
                return httpx.Response(
                    200, json=_envelope({"source_id": "src_held", "held": True})
                )
            raise AssertionError("the client must not upload held content")

        client, recorder = _client(responder)
        result = client.upload(self.media)

        self.assertEqual(len(recorder.requests), 1)
        self.assertTrue(result["data"]["skipped_upload"])
        self.assertEqual(result["data"]["source_id"], "src_held")

    def test_force_upload_skips_the_preflight(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sources/by-digest/"):
                raise AssertionError("preflight must be skipped")
            return httpx.Response(202, json=_envelope({"source_id": "src_x"}))

        client, recorder = _client(responder)
        client.upload(self.media, skip_if_held=False)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(recorder.requests[0].url.path, "/v1/uploads")

    def test_filename_header_cannot_forge_another_header(self) -> None:
        """CRLF in a filename would otherwise inject a header of the attacker's choosing."""
        from vorquel_watch.remote_client import _header_safe

        self.assertEqual(_header_safe("evil\r\nx-admin: 1"), "evilx-admin: 1")
        self.assertEqual(_header_safe("\r\n"), "media")
        self.assertEqual(len(_header_safe("n" * 500)), 200)

    def test_ordinary_filename_is_sent_as_metadata(self) -> None:
        media = Path(self._tmp.name) / "a.mp4"
        media.write_bytes(CONTENT)

        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/v1/sources/by-digest/"):
                return httpx.Response(404, json=_envelope({"ok": False, "error": {}}))
            return httpx.Response(202, json=_envelope({"source_id": "src_x"}))

        client, recorder = _client(responder)
        client.upload(media)
        self.assertEqual(recorder.requests[-1].headers["x-vorquel-filename"], "a.mp4")

    def test_empty_file_is_refused_before_any_request(self) -> None:
        empty = Path(self._tmp.name) / "empty.mp4"
        empty.write_bytes(b"")

        def responder(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        client, _ = _client(responder)
        with self.assertRaises(RemoteClientError):
            client.upload(empty)

    def test_missing_file_raises_before_any_request(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made")

        client, _ = _client(responder)
        with self.assertRaises(FileNotFoundError):
            client.upload(Path(self._tmp.name) / "absent.mp4")


class ErrorSurfaceTests(unittest.TestCase):
    def test_unauthorized_is_reported_without_echoing_the_token(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json=_envelope({"ok": False, "error": {}}))

        client, _ = _client(responder)
        with self.assertRaises(RemoteClientError) as caught:
            client.get_job("job_x")
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_transport_failure_does_not_leak_the_url(self) -> None:
        """httpx messages embed the full URL, which is operator configuration."""

        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("failed connecting to http://secret-host.invalid")

        client, _ = _client(responder)
        with self.assertRaises(RemoteClientError) as caught:
            client.health()
        message = str(caught.exception)
        self.assertNotIn("secret-host", message)
        self.assertIn("ConnectError", message)

    def test_non_json_response_is_reported_cleanly(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>gateway</html>")

        client, _ = _client(responder)
        with self.assertRaises(RemoteClientError) as caught:
            client.list_jobs()
        self.assertIn("502", str(caught.exception))

    def test_http_status_is_surfaced_for_the_caller(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json=_envelope({"ok": False, "error": {}}))

        client, _ = _client(responder)
        payload = client.get_job("job_x")
        self.assertEqual(payload["_http_status"], 404)


class QueryTests(unittest.TestCase):
    def test_list_jobs_passes_filters_as_query_parameters(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope({"items": []}))

        client, recorder = _client(responder)
        client.list_jobs(limit=7, status="QUEUED", cursor="abc")
        params = recorder.requests[-1].url.params
        self.assertEqual(params["limit"], "7")
        self.assertEqual(params["status"], "QUEUED")
        self.assertEqual(params["cursor"], "abc")

    def test_cancel_posts_to_the_cancel_route(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_envelope({"status": "CANCELLED"}))

        client, recorder = _client(responder)
        client.cancel_job("job_abc")
        self.assertEqual(recorder.requests[-1].method, "POST")
        self.assertEqual(recorder.requests[-1].url.path, "/v1/jobs/job_abc/cancel")


class StreamingTests(unittest.TestCase):
    def test_body_is_streamed_rather_than_read_whole(self) -> None:
        """A multi-gigabyte upload must not become a multi-gigabyte allocation.

        The client hands httpx an iterator over the file, so the request body is
        produced in chunks. Asserting the generator type keeps a future
        `path.read_bytes()` refactor from silently reintroducing the whole-file
        load.
        """
        from vorquel_watch.remote_client import _iter_file

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "big.mp4"
            media.write_bytes(CONTENT)
            stream = _iter_file(media)
            self.assertFalse(isinstance(stream, (bytes, bytearray)))
            self.assertEqual(b"".join(stream), CONTENT)


if __name__ == "__main__":
    unittest.main()
