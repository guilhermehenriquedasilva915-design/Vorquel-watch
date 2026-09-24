"""Trust boundaries and failure modes of the remote processing API.

The API accepts untrusted bytes from the network and turns them into work on the
VPS. These tests hold the line on three things:

- nothing happens without a valid token, on any route that does anything;
- an upload cannot exceed its limits, lie about its size or its digest, or smuggle
  a filename into a path;
- the job is persisted and queued, never processed inline, so the notebook can
  disconnect the moment it has identifiers.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from vorquel_watch.config import Settings
from vorquel_watch.envelope import envelope, safe_error
from vorquel_watch.remote_api import create_app, purge_stale_uploads


TOKEN = "k" * 48
AUTH = {"authorization": f"Bearer {TOKEN}"}

PAYLOAD = b"fake-media-bytes-that-are-never-decoded" * 4
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def _settings(data_dir: Path, **overrides: Any) -> Settings:
    base = {
        "data_dir": data_dir,
        "supabase_url": "https://example.invalid",
        "supabase_secret_key": "test-secret",
    }
    base.update(overrides)
    return Settings(**base)


class FakeRepo:
    def __init__(self, held: dict[str, Any] | None = None) -> None:
        self.held = held
        self.listed: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def find_source_by_hash(self, content_sha256: str) -> dict[str, Any] | None:
        if self.held and self.held["content_sha256"] == content_sha256:
            return self.held
        return None

    def list_jobs(self, *, limit: int = 20, cursor=None, status=None) -> dict[str, Any]:
        self.list_calls.append({"limit": limit, "cursor": cursor, "status": status})
        if status and status.strip().upper() not in {
            "QUEUED",
            "RUNNING",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
        }:
            raise ValueError("invalid status")
        return {"items": self.listed, "next_cursor": None}


class FakeService:
    """Stands in for WatchService, returning the same envelope shapes it does."""

    def __init__(self, repo: FakeRepo) -> None:
        self.repo = repo
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.job_status = "QUEUED"

    def start_analysis(self, source_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.started.append(source_id)
        return envelope(
            "start_analysis",
            {
                "job": {
                    "job_id": "job_" + "a" * 32,
                    "source_id": source_id,
                    "status": self.job_status,
                    "stage": "QUEUED",
                },
                "reused": False,
            },
            contains_untrusted_content=False,
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        if not job_id.startswith("job_"):
            return safe_error("get_job", "INVALID_ARGUMENT", "Invalid job id.")
        if job_id == "job_" + "z" * 32:
            return safe_error("get_job", "NOT_FOUND", "Job not found.")
        return envelope(
            "get_job",
            {"job_id": job_id, "status": "RUNNING", "stage": "TRANSCRIBING"},
            contains_untrusted_content=False,
        )

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        self.cancelled.append(job_id)
        if job_id == "job_" + "z" * 32:
            return safe_error("cancel_job", "NOT_FOUND", "Job not found.")
        return envelope(
            "cancel_job",
            {"job_id": job_id, "status": "CANCELLED"},
            contains_untrusted_content=False,
        )


class RemoteApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repo = FakeRepo()
        self.service = FakeService(self.repo)
        self.ingest_calls: list[dict[str, Any]] = []

    def build(self, *, ingest=None, **setting_overrides) -> TestClient:
        def default_ingest(path, settings, *, original_filename=None):
            self.ingest_calls.append(
                {
                    "path": Path(path),
                    "original_filename": original_filename,
                    "size": Path(path).stat().st_size,
                }
            )
            return {
                "source_id": "src_" + "b" * 32,
                "reused": False,
                "duration_ms": 35300,
                "source_kind": "LOCAL_VIDEO",
            }

        app = create_app(
            settings=_settings(self.data_dir, **setting_overrides),
            service=self.service,
            token=TOKEN,
            ingest=ingest or default_ingest,
        )
        return TestClient(app)


class AuthenticationBoundaryTests(RemoteApiTestCase):
    def test_every_working_route_requires_a_token(self) -> None:
        """The absence of a token must stop the request before it does anything."""
        client = self.build()
        calls = [
            ("post", "/v1/uploads"),
            ("get", f"/v1/sources/by-digest/{DIGEST}"),
            ("get", "/v1/jobs"),
            ("get", "/v1/jobs/job_" + "a" * 32),
            ("post", "/v1/jobs/job_" + "a" * 32 + "/cancel"),
        ]
        for method, path in calls:
            response = getattr(client, method)(path)
            self.assertEqual(response.status_code, 401, f"{method} {path}")
            self.assertEqual(
                response.json()["data"]["error"]["code"], "UNAUTHORIZED", path
            )

        # Nothing reached the service behind the guard.
        self.assertEqual(self.service.started, [])
        self.assertEqual(self.service.cancelled, [])
        self.assertEqual(self.ingest_calls, [])

    def test_wrong_token_is_rejected(self) -> None:
        client = self.build()
        response = client.get(
            "/v1/jobs", headers={"authorization": "Bearer " + "x" * 48}
        )
        self.assertEqual(response.status_code, 401)

    def test_unauthenticated_upload_never_writes_to_disk(self) -> None:
        """A rejected caller must not be able to spend the host's disk."""
        client = self.build()
        client.post("/v1/uploads", content=PAYLOAD)
        incoming = self.data_dir / "incoming"
        leftovers = list(incoming.glob("*")) if incoming.exists() else []
        self.assertEqual(leftovers, [])

    def test_health_is_public_and_reveals_nothing(self) -> None:
        client = self.build()
        response = client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})


class UploadAcceptanceTests(RemoteApiTestCase):
    def test_valid_upload_registers_source_and_queues_job(self) -> None:
        client = self.build()
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "x-vorquel-content-sha256": DIGEST},
        )
        self.assertEqual(response.status_code, 202)
        data = response.json()["data"]
        self.assertEqual(data["source_id"], "src_" + "b" * 32)
        self.assertEqual(data["job_id"], "job_" + "a" * 32)
        self.assertEqual(data["content_sha256"], DIGEST)
        self.assertEqual(data["received_bytes"], len(PAYLOAD))
        self.assertEqual(self.service.started, ["src_" + "b" * 32])

    def test_queued_job_is_returned_rather_than_a_finished_one(self) -> None:
        """The notebook may disconnect immediately.

        The response must describe queued work, not completed work: processing is
        the persistent worker's job, not the request's.
        """
        client = self.build()
        response = client.post(
            "/v1/uploads", content=PAYLOAD, headers=AUTH
        )
        self.assertEqual(response.json()["data"]["job_status"], "QUEUED")

    def test_partial_upload_is_not_left_on_disk(self) -> None:
        client = self.build()
        client.post("/v1/uploads", content=PAYLOAD, headers=AUTH)
        leftovers = list((self.data_dir / "incoming").glob("*.part"))
        self.assertEqual(leftovers, [])

    def test_untrusted_filename_never_becomes_the_stored_path(self) -> None:
        """A traversal attempt is metadata, not a location.

        The header is passed through to ingest, which cleans it and files the
        object under its digest. What matters here is that the temporary file the
        API wrote is not named from the header.
        """
        client = self.build()
        hostile = "../../../../etc/cron.d/pwned"
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "x-vorquel-filename": hostile},
        )
        self.assertEqual(response.status_code, 202)
        call = self.ingest_calls[0]
        self.assertEqual(call["original_filename"], hostile)
        # The path the API chose is a uuid .part inside incoming/, not the header.
        self.assertTrue(call["path"].name.endswith(".part"))
        self.assertEqual(call["path"].parent, self.data_dir / "incoming")
        self.assertNotIn("pwned", str(call["path"]))

    def test_response_carries_no_host_path(self) -> None:
        client = self.build()
        response = client.post("/v1/uploads", content=PAYLOAD, headers=AUTH)
        body = response.text
        for leak in (str(self.data_dir), "incoming", ".part"):
            self.assertNotIn(leak, body)


class UploadRejectionTests(RemoteApiTestCase):
    def test_missing_content_length_is_refused(self) -> None:
        """The size limit has to be applicable before the body is read."""
        client = self.build()
        response = client.post(
            "/v1/uploads",
            content=iter([PAYLOAD]),  # chunked: no Content-Length
            headers=AUTH,
        )
        self.assertEqual(response.status_code, 411)
        self.assertEqual(self.ingest_calls, [])

    def test_oversized_declaration_is_refused_before_transfer(self) -> None:
        client = self.build(max_source_bytes=len(PAYLOAD) - 1)
        response = client.post(
            "/v1/uploads", content=PAYLOAD, headers=AUTH
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json()["data"]["error"]["code"], "SOURCE_TOO_LARGE"
        )
        self.assertEqual(self.ingest_calls, [])

    def test_empty_upload_is_refused(self) -> None:
        client = self.build()
        response = client.post("/v1/uploads", content=b"", headers=AUTH)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.ingest_calls, [])

    def test_malformed_digest_header_is_refused(self) -> None:
        client = self.build()
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "x-vorquel-content-sha256": "not-a-digest"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.ingest_calls, [])

    def test_digest_mismatch_is_refused_and_nothing_is_ingested(self) -> None:
        """Corrupted transfer must not become a transcript."""
        client = self.build()
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "x-vorquel-content-sha256": "a" * 64},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["data"]["error"]["code"], "DIGEST_MISMATCH"
        )
        self.assertEqual(self.ingest_calls, [])
        self.assertEqual(self.service.started, [])

    def test_media_rejected_by_the_guard_returns_415(self) -> None:
        """An unsupported container is a rejection, not a server fault."""

        def refusing_ingest(path, settings, *, original_filename=None):
            raise ValueError("container format is not allowed")

        client = self.build(ingest=refusing_ingest)
        response = client.post("/v1/uploads", content=PAYLOAD, headers=AUTH)
        self.assertEqual(response.status_code, 415)
        self.assertEqual(
            response.json()["data"]["error"]["code"], "MEDIA_REJECTED"
        )
        self.assertEqual(self.service.started, [])

    def test_ingest_failure_is_reported_without_internals(self) -> None:
        def exploding_ingest(path, settings, *, original_filename=None):
            raise RuntimeError(r"C:\secret\path\leaked.media")

        client = self.build(ingest=exploding_ingest)
        response = client.post("/v1/uploads", content=PAYLOAD, headers=AUTH)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("leaked", response.text)
        self.assertNotIn("secret", response.text)

    def test_a_lying_content_length_is_still_capped_mid_stream(self) -> None:
        """Content-Length is a claim, not a guarantee.

        A client that declares a small body and then streams a huge one must be
        cut off while streaming, not after the disk has filled. The declared
        length passes the up-front check here, so only the in-stream cap can stop
        it.
        """
        limit = 4096
        client = self.build(max_source_bytes=limit)
        oversized = b"x" * (limit * 8)
        response = client.post(
            "/v1/uploads",
            content=oversized,
            headers={**AUTH, "content-length": str(limit)},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json()["data"]["error"]["code"], "SOURCE_TOO_LARGE"
        )
        self.assertEqual(self.ingest_calls, [])
        # The aborted transfer must not be left on disk.
        self.assertEqual(list((self.data_dir / "incoming").glob("*.part")), [])

    def test_a_truncated_body_is_refused_rather_than_transcribed(self) -> None:
        """A short read is a failed transfer.

        Accepting it would produce a source whose transcript silently stops early,
        which is worse than an error because nothing downstream would flag it.
        """
        client = self.build()
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "content-length": str(len(PAYLOAD) + 999)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["data"]["error"]["code"], "LENGTH_MISMATCH"
        )
        self.assertEqual(self.ingest_calls, [])

    def test_insufficient_free_space_is_refused(self) -> None:
        # A limit far above any real disk forces the free-space guard to fail.
        client = self.build(max_source_bytes=1 << 62)
        response = client.post(
            "/v1/uploads",
            content=PAYLOAD,
            headers={**AUTH, "content-length": str(1 << 61)},
        )
        self.assertEqual(response.status_code, 507)
        self.assertEqual(self.ingest_calls, [])


class DeduplicationTests(RemoteApiTestCase):
    def test_held_digest_is_reported_without_an_upload(self) -> None:
        """Re-sending a workshop recording must not cost its bytes again."""
        self.repo.held = {
            "source_id": "src_" + "c" * 32,
            "content_sha256": DIGEST,
            "duration_ms": 1_629_994,
            "source_kind": "LOCAL_VIDEO",
        }
        client = self.build()
        response = client.get(f"/v1/sources/by-digest/{DIGEST}", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["source_id"], "src_" + "c" * 32)
        self.assertTrue(data["held"])

    def test_unheld_digest_is_a_clean_404(self) -> None:
        client = self.build()
        response = client.get(f"/v1/sources/by-digest/{DIGEST}", headers=AUTH)
        self.assertEqual(response.status_code, 404)

    def test_malformed_digest_lookup_is_refused(self) -> None:
        client = self.build()
        response = client.get("/v1/sources/by-digest/zzz", headers=AUTH)
        self.assertEqual(response.status_code, 400)

    def test_reused_source_is_reported_as_reused(self) -> None:
        """Ingest deduplicates on the digest it computed; the API reports that."""

        def reusing_ingest(path, settings, *, original_filename=None):
            return {
                "source_id": "src_" + "c" * 32,
                "reused": True,
                "duration_ms": 1_629_994,
                "source_kind": "LOCAL_VIDEO",
            }

        client = self.build(ingest=reusing_ingest)
        response = client.post("/v1/uploads", content=PAYLOAD, headers=AUTH)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["data"]["reused_source"])


class JobQueryTests(RemoteApiTestCase):
    def test_status_returns_the_job(self) -> None:
        client = self.build()
        job_id = "job_" + "a" * 32
        response = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["job_id"], job_id)

    def test_unknown_job_is_404(self) -> None:
        client = self.build()
        response = client.get("/v1/jobs/job_" + "z" * 32, headers=AUTH)
        self.assertEqual(response.status_code, 404)

    def test_invalid_job_id_is_400(self) -> None:
        client = self.build()
        response = client.get("/v1/jobs/not-a-job", headers=AUTH)
        self.assertEqual(response.status_code, 400)

    def test_list_passes_bounded_arguments_through(self) -> None:
        client = self.build()
        response = client.get("/v1/jobs?limit=5&status=queued", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.repo.list_calls[0], {"limit": 5, "cursor": None, "status": "queued"}
        )

    def test_list_rejects_a_non_numeric_limit(self) -> None:
        client = self.build()
        response = client.get("/v1/jobs?limit=lots", headers=AUTH)
        self.assertEqual(response.status_code, 400)

    def test_list_rejects_an_unknown_status(self) -> None:
        client = self.build()
        response = client.get("/v1/jobs?status=PWNED", headers=AUTH)
        self.assertEqual(response.status_code, 400)

    def test_cancel_reaches_the_service(self) -> None:
        client = self.build()
        job_id = "job_" + "a" * 32
        response = client.post(f"/v1/jobs/{job_id}/cancel", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.cancelled, [job_id])

    def test_cancelling_an_unknown_job_is_404(self) -> None:
        client = self.build()
        response = client.post("/v1/jobs/job_" + "z" * 32 + "/cancel", headers=AUTH)
        self.assertEqual(response.status_code, 404)


class RestartRecoveryTests(RemoteApiTestCase):
    def test_stale_partial_uploads_are_purged(self) -> None:
        """A process killed mid-upload leaves bytes nothing will ever finish."""
        incoming = self.data_dir / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        (incoming / "dead1.part").write_bytes(b"x")
        (incoming / "dead2.part").write_bytes(b"y")
        keep = incoming / "notes.txt"
        keep.write_bytes(b"z")

        removed = purge_stale_uploads(self.data_dir)

        self.assertEqual(removed, 2)
        self.assertEqual(list(incoming.glob("*.part")), [])
        self.assertTrue(keep.exists())

    def test_purge_on_a_missing_directory_is_harmless(self) -> None:
        fresh = self.data_dir / "never-used"
        self.assertEqual(purge_stale_uploads(fresh), 0)

    def test_creating_the_app_purges_stale_uploads(self) -> None:
        incoming = self.data_dir / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        (incoming / "dead.part").write_bytes(b"x")
        self.build()
        self.assertEqual(list(incoming.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
