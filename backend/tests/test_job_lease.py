"""Worker lease handling (FASE 5 groundwork, and the open half of INT-02).

The database guarantees are proven against the live project. These cover the
client side: how an empty queue is recognised, and what the worker does when it
loses a job it was already working on.
"""

import unittest
from unittest import mock

from vorquel_watch.db import DEFAULT_LEASE_SECONDS, WatchRepository
from vorquel_watch.worker import new_worker_id, process_one


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    """The real client chains .rpc(...).execute(), so the double must too."""

    def __init__(self, data):
        self._data = data

    def execute(self) -> _Response:
        return _Response(self._data)


class _FakeClient:
    """Stands in for the supabase client at the rpc boundary."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name, params) -> _Rpc:
        self.calls.append((name, params))
        return _Rpc(self.responses.get(name))


def _repo(responses: dict) -> WatchRepository:
    repo = object.__new__(WatchRepository)
    repo.client = _FakeClient(responses)
    return repo


class ClaimParsingTests(unittest.TestCase):
    """An empty queue does not come back as an empty result.

    claim_next_job returns a composite, so PostgREST reports a row whose columns
    are all NULL. Treating that row's presence as a claim would hand the worker
    a job that does not exist.
    """

    def test_all_null_composite_is_not_a_job(self) -> None:
        for payload in (
            {"job_id": None, "status": None, "worker_id": None},
            [{"job_id": None}],
            None,
            [],
            {},
            "",
        ):
            with self.subTest(payload=payload):
                repo = _repo({"claim_next_job": payload})
                self.assertIsNone(repo.claim_next_job("wkr_test"))

    def test_a_real_job_is_returned(self) -> None:
        job = {"job_id": "job_abc", "status": "RUNNING", "worker_id": "wkr_test"}
        for payload in (job, [job]):
            with self.subTest(payload=payload):
                repo = _repo({"claim_next_job": payload})
                self.assertEqual(repo.claim_next_job("wkr_test"), job)

    def test_claim_passes_the_worker_identity_and_lease(self) -> None:
        repo = _repo({"claim_next_job": None})
        repo.claim_next_job("wkr_test", 45)

        name, params = repo.client.calls[0]
        self.assertEqual(name, "claim_next_job")
        self.assertEqual(params["p_worker_id"], "wkr_test")
        self.assertEqual(params["p_lease_seconds"], 45)


class HeartbeatTests(unittest.TestCase):
    def test_falsey_response_means_the_job_was_lost(self) -> None:
        for payload in (False, None, ""):
            with self.subTest(payload=payload):
                repo = _repo({"heartbeat_job": payload})
                self.assertFalse(repo.heartbeat("job_abc", "wkr_test"))

    def test_true_response_means_the_lease_was_extended(self) -> None:
        repo = _repo({"heartbeat_job": True})
        self.assertTrue(repo.heartbeat("job_abc", "wkr_test"))

    def test_default_lease_is_used_when_unspecified(self) -> None:
        repo = _repo({"heartbeat_job": True})
        repo.heartbeat("job_abc", "wkr_test")
        self.assertEqual(
            repo.client.calls[0][1]["p_lease_seconds"], DEFAULT_LEASE_SECONDS
        )


class WorkerIdentityTests(unittest.TestCase):
    def test_worker_id_carries_nothing_about_the_machine(self) -> None:
        """Worker ids reach the database and the logs."""
        import os
        import socket

        worker_id = new_worker_id()
        self.assertTrue(worker_id.startswith("wkr_"))
        self.assertNotIn(socket.gethostname().lower(), worker_id.lower())
        self.assertNotIn(str(os.getpid()), worker_id)
        self.assertNotEqual(worker_id, new_worker_id())


class LostLeaseTests(unittest.TestCase):
    """A result produced after the lease was lost must not be written."""

    def setUp(self) -> None:
        self.job = {"job_id": "job_abc", "source_id": "src_abc", "mode": "FAST"}
        self.transcript = {"transcript_id": "trn_abc"}

    def _repo_mock(self, heartbeat_result: bool) -> mock.Mock:
        repo = mock.Mock()
        repo.claim_next_job.return_value = self.job
        repo.heartbeat.return_value = heartbeat_result
        return repo

    def _engine_mock(self) -> mock.Mock:
        return mock.Mock()

    def test_result_is_discarded_when_the_lease_was_lost(self) -> None:
        repo = self._repo_mock(heartbeat_result=False)

        with mock.patch(
            "vorquel_watch.worker.transcribe_resumable",
            return_value=self.transcript,
        ):
            self.assertTrue(process_one(repo, self._engine_mock(), "wkr_test"))

        repo.complete_job.assert_not_called()
        repo.set_terminal_status.assert_not_called()

    def test_result_is_written_when_the_lease_still_holds(self) -> None:
        repo = self._repo_mock(heartbeat_result=True)

        with mock.patch(
            "vorquel_watch.worker.transcribe_resumable",
            return_value=self.transcript,
        ):
            self.assertTrue(process_one(repo, self._engine_mock(), "wkr_test"))

        repo.complete_job.assert_called_once_with("job_abc", "trn_abc")

    def test_an_empty_queue_is_reported_as_no_work(self) -> None:
        repo = mock.Mock()
        repo.claim_next_job.return_value = None

        self.assertFalse(process_one(repo, self._engine_mock(), "wkr_test"))
        repo.complete_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
