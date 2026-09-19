"""Service-layer boundary behaviour (SEC-08 enforcement and INT-04).

These use a recording fake repository: a malformed identifier must be
rejected before any query is issued, and a transcript whose job did not
succeed must never be served as a final result.
"""

import unittest
from pathlib import Path

from vorquel_watch.config import Settings
from vorquel_watch.service import WatchService


def _settings() -> Settings:
    return Settings(
        data_dir=Path("."),
        supabase_url="https://example.invalid",
        supabase_secret_key="not-a-real-key",
    )


class RecordingRepo:
    """Records every call so tests can assert the repository was never reached."""

    def __init__(self, **returns: object) -> None:
        self.calls: list[str] = []
        self._returns = returns

    def __getattr__(self, name: str):
        def _call(*args: object, **kwargs: object):
            self.calls.append(name)
            value = self._returns.get(name)
            return value() if callable(value) else value

        return _call


HOSTILE_IDS = [
    "../../etc/passwd",
    "src_../../etc/passwd",
    "src_abc'; drop table sources;--",
    "src_abc def",
    "src_abc\x00",
    "src_" + "a" * 400,
    "job_abc",
    "",
    None,
    12345,
]


class IdentifierRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RecordingRepo()
        self.service = WatchService(settings=_settings(), repo=self.repo)

    def _assert_rejected(self, payload: dict) -> None:
        self.assertFalse(payload["data"]["ok"])
        self.assertEqual(payload["data"]["error"]["code"], "INVALID_ARGUMENT")

    def test_malformed_ids_never_reach_the_repository(self) -> None:
        for value in HOSTILE_IDS:
            with self.subTest(value=repr(value)):
                self.repo.calls.clear()
                self._assert_rejected(self.service.get_source(value))
                self.assertEqual(self.repo.calls, [])

    def test_every_id_taking_tool_validates_its_prefix(self) -> None:
        # Each call passes an id carrying the wrong prefix on purpose.
        cases = [
            (self.service.get_source, "job_abc"),
            (self.service.start_analysis, "job_abc"),
            (self.service.get_job, "src_abc"),
            (self.service.cancel_job, "src_abc"),
            (self.service.get_transcript, "src_abc"),
            (self.service.get_segment, "src_abc"),
            (self.service.list_speakers, "job_abc"),
            (self.service.get_speaker_turns, "src_abc"),
            (self.service.list_artifacts, "job_abc"),
            (self.service.get_artifact, "src_abc"),
        ]
        for call, value in cases:
            with self.subTest(tool=call.__name__):
                self.repo.calls.clear()
                self._assert_rejected(call(value))
                self.assertEqual(self.repo.calls, [])

    def test_create_export_validates_transcript_id(self) -> None:
        self._assert_rejected(self.service.create_export("src_abc", "SRT"))
        self.assertEqual(self.repo.calls, [])

    def test_search_rejects_hostile_and_oversized_source_id_lists(self) -> None:
        for value in ([], ["src_ok", "../../etc"], ["job_abc"], "src_abc", None):
            with self.subTest(value=repr(value)):
                self.repo.calls.clear()
                self._assert_rejected(self.service.search_transcript("q", value))
                self.assertEqual(self.repo.calls, [])

    def test_search_rejects_more_than_twenty_five_sources(self) -> None:
        ids = [f"src_{index:04d}" for index in range(26)]
        self._assert_rejected(self.service.search_transcript("q", ids))
        self.assertEqual(self.repo.calls, [])

    def test_error_message_never_echoes_the_payload(self) -> None:
        payload = "src_abc'; drop table sources;--"
        result = self.service.get_source(payload)
        message = result["data"]["error"]["message"]
        self.assertNotIn(payload, message)
        self.assertNotIn("drop table", message.lower())


class PartialTranscriptTests(unittest.TestCase):
    """INT-04: output of a job that did not succeed is not a final result."""

    def _service(self, job_status: str | None) -> WatchService:
        repo = RecordingRepo(
            get_transcript_meta=lambda: {
                "transcript_id": "trn_abc",
                "source_id": "src_abc",
                "job_id": "job_abc",
            },
            get_job=(lambda: {"job_id": "job_abc", "status": job_status})
            if job_status
            else (lambda: None),
            get_transcript_segments=lambda: {"items": [], "next_cursor": None},
            get_segment=lambda: {
                "target_segment_id": "seg_abc",
                "segments": [{"transcript_id": "trn_abc", "ordinal": 0}],
            },
        )
        return WatchService(settings=_settings(), repo=repo)

    def test_non_succeeded_job_blocks_get_transcript(self) -> None:
        for status in ("RUNNING", "FAILED", "CANCELLED", "QUEUED", None):
            with self.subTest(status=status):
                result = self._service(status).get_transcript("trn_abc")
                self.assertEqual(
                    result["data"]["error"]["code"], "TRANSCRIPT_NOT_FINAL"
                )

    def test_non_succeeded_job_blocks_get_segment(self) -> None:
        result = self._service("FAILED").get_segment("seg_abc")
        self.assertEqual(result["data"]["error"]["code"], "TRANSCRIPT_NOT_FINAL")

    def test_non_succeeded_job_blocks_create_export(self) -> None:
        result = self._service("CANCELLED").create_export("trn_abc", "SRT")
        self.assertEqual(result["data"]["error"]["code"], "TRANSCRIPT_NOT_FINAL")

    def test_succeeded_job_is_served(self) -> None:
        result = self._service("SUCCEEDED").get_transcript("trn_abc")
        self.assertNotIn("error", result["data"])
        self.assertEqual(result["data"]["transcript"]["transcript_id"], "trn_abc")


if __name__ == "__main__":
    unittest.main()
