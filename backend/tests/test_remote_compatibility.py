"""Remote Processing must not change what already worked.

Remote is an additional way to hand the Watch a file. It is not a new pipeline,
not a new MCP capability and not a replacement for local mode. These tests fail
if that stops being true.
"""

from __future__ import annotations

import asyncio
import unittest

from mcp import Client

from vorquel_watch.cli import build_parser
from vorquel_watch.db import _JOB_MCP_COLUMNS, WatchRepository
from vorquel_watch.mcp_server import mcp


# The frozen V1 surface plus the versioned knowledge and visual-context
# extensions. Remote Processing adds nothing to it.
EXPECTED_MCP_TOOL_COUNT = 24


class McpSurfaceIsUnchangedTests(unittest.TestCase):
    def _tool_names(self) -> set[str]:
        async def run() -> set[str]:
            async with Client(mcp) as client:
                return {tool.name for tool in (await client.list_tools()).tools}

        return asyncio.run(run())

    def test_tool_count_is_still_twenty_four(self) -> None:
        self.assertEqual(len(self._tool_names()), EXPECTED_MCP_TOOL_COUNT)

    def test_remote_processing_exposes_no_mcp_tool(self) -> None:
        """Uploading and cancelling are operator actions, not model actions.

        Putting them on the MCP surface would let a model that misread a hostile
        transcript spend the VPS's disk or kill a running job.
        """
        names = self._tool_names()
        for forbidden in (
            "remote_upload",
            "upload",
            "remote_status",
            "remote_list",
            "remote_cancel",
            "cancel_remote_job",
        ):
            self.assertNotIn(forbidden, names)

    def test_no_mcp_tool_accepts_a_path_or_url_after_remote_was_added(self) -> None:
        async def run() -> list:
            async with Client(mcp) as client:
                return (await client.list_tools()).tools

        forbidden = {"path", "file", "filename", "url", "uri", "token", "secret"}
        offences: list[str] = []
        for tool in asyncio.run(run()):
            schema = (
                getattr(tool, "input_schema", None)
                or getattr(tool, "inputSchema", None)
                or {}
            )
            for argument in (schema.get("properties") or {}):
                if argument.lower() in forbidden:
                    offences.append(f"{tool.name}.{argument}")
        self.assertEqual(offences, [])

    def test_get_frame_is_still_absent(self) -> None:
        self.assertNotIn("get_frame", self._tool_names())


class LocalCliStillWorksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_local_commands_are_still_parsed(self) -> None:
        """The commands that existed before remote must keep working unchanged."""
        cases = [
            (["ingest", "video.mp4"], {"path": "video.mp4"}),
            (["analyze", "video.mp4"], {"path": "video.mp4"}),
            (["worker", "--once"], {"once": True}),
        ]
        for argv, expected in cases:
            args = self.parser.parse_args(argv)
            for key, value in expected.items():
                self.assertEqual(getattr(args, key), value, argv)
            self.assertTrue(callable(args.func), argv)

    def test_remote_commands_are_available(self) -> None:
        for argv in (
            ["remote", "upload", "video.mp4"],
            ["remote", "status", "job_abc"],
            ["remote", "list"],
            ["remote", "cancel", "job_abc"],
            ["remote", "configure"],
        ):
            args = self.parser.parse_args(argv)
            self.assertTrue(callable(args.func), argv)

    def test_remote_requires_a_subcommand(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["remote"])

    def test_ingest_does_not_gain_a_remote_flag(self) -> None:
        """Local ingest must not quietly become a network operation."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["ingest", "video.mp4", "--url", "http://x.invalid"])


class JobListingProjectionTests(unittest.TestCase):
    """The remote listing must not widen what leaves the database."""

    class _Table:
        def __init__(self, recorder: dict) -> None:
            self._recorder = recorder

        def select(self, columns: str):
            self._recorder["columns"] = columns
            return self

        def order(self, *_args, **_kwargs):
            return self

        def eq(self, field: str, value):
            self._recorder.setdefault("filters", []).append((field, value))
            return self

        def range(self, start: int, end: int):
            self._recorder["range"] = (start, end)
            return self

        def execute(self):
            return type("Result", (), {"data": []})()

    class _Client:
        def __init__(self, recorder: dict) -> None:
            self._recorder = recorder

        def table(self, name: str):
            self._recorder["table"] = name
            return JobListingProjectionTests._Table(self._recorder)

    def _repo(self, recorder: dict) -> WatchRepository:
        repo = WatchRepository.__new__(WatchRepository)
        repo.client = self._Client(recorder)
        return repo

    def test_listing_uses_the_same_projection_as_get_job(self) -> None:
        recorder: dict = {}
        self._repo(recorder).list_jobs(limit=5)
        self.assertEqual(recorder["table"], "processing_jobs")
        self.assertEqual(recorder["columns"], _JOB_MCP_COLUMNS)

    def test_lease_and_worker_columns_stay_internal(self) -> None:
        """worker_id and the lease are operational state, not client data."""
        recorder: dict = {}
        self._repo(recorder).list_jobs(limit=5)
        for internal in ("worker_id", "lease_expires_at", "heartbeat_at", "attempt"):
            self.assertNotIn(internal, recorder["columns"])

    def test_unknown_status_is_refused_before_a_query_runs(self) -> None:
        recorder: dict = {}
        with self.assertRaises(ValueError):
            self._repo(recorder).list_jobs(status="'; drop table sources; --")
        self.assertNotIn("range", recorder)

    def test_limit_is_clamped(self) -> None:
        recorder: dict = {}
        self._repo(recorder).list_jobs(limit=10_000)
        start, end = recorder["range"]
        self.assertEqual(start, 0)
        self.assertLessEqual(end - start + 1, 100)

    def test_invalid_cursor_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._repo({}).list_jobs(cursor="not-a-cursor")


if __name__ == "__main__":
    unittest.main()
