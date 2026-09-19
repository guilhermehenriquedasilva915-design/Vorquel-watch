import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from vorquel_watch import cli


class AnalyzeCommandTests(unittest.TestCase):
    def _args(self, path="video.mp4", language="pt"):
        return argparse.Namespace(path=path, language=language)

    @mock.patch("vorquel_watch.cli.run_worker")
    @mock.patch("vorquel_watch.cli.configure_logging")
    @mock.patch("vorquel_watch.cli.WatchService")
    @mock.patch("vorquel_watch.cli.ingest_local_file")
    @mock.patch("vorquel_watch.cli.Settings.from_env")
    def test_new_queued_analysis_runs_worker_and_returns_final_job(
        self, from_env, ingest, service_cls, configure_logging, run_worker
    ):
        settings = object()
        from_env.return_value = settings
        ingest.return_value = {
            "source_id": "src_abc",
            "reused": False,
            "duration_ms": 1000,
            "source_kind": "LOCAL_VIDEO",
        }

        service = service_cls.return_value
        service.start_analysis.return_value = {
            "data": {
                "job": {"job_id": "job_abc", "status": "QUEUED"},
                "reused": False,
            }
        }
        service.get_job.return_value = {
            "data": {"job_id": "job_abc", "status": "SUCCEEDED", "stage": "COMPLETE"}
        }

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.cmd_analyze(self._args())

        self.assertEqual(code, 0)
        configure_logging.assert_called_once_with()
        run_worker.assert_called_once_with(once=True)
        service.start_analysis.assert_called_once_with(
            source_id="src_abc", mode="FAST", language_hint="pt"
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["analysis"]["job"]["status"], "SUCCEEDED")
        self.assertFalse(payload["analysis"]["reused"])

    @mock.patch("vorquel_watch.cli.run_worker")
    @mock.patch("vorquel_watch.cli.WatchService")
    @mock.patch("vorquel_watch.cli.ingest_local_file")
    @mock.patch("vorquel_watch.cli.Settings.from_env")
    def test_reused_succeeded_analysis_skips_worker(
        self, from_env, ingest, service_cls, run_worker
    ):
        from_env.return_value = object()
        ingest.return_value = {
            "source_id": "src_abc",
            "reused": True,
            "duration_ms": 1000,
            "source_kind": "LOCAL_VIDEO",
        }
        service = service_cls.return_value
        service.start_analysis.return_value = {
            "data": {
                "job": {"job_id": "job_abc", "status": "SUCCEEDED", "stage": "COMPLETE"},
                "reused": True,
            }
        }

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.cmd_analyze(self._args())

        self.assertEqual(code, 0)
        run_worker.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["analysis"]["reused"])

    @mock.patch("vorquel_watch.cli.WatchService")
    @mock.patch("vorquel_watch.cli.ingest_local_file")
    @mock.patch("vorquel_watch.cli.Settings.from_env")
    def test_service_error_is_returned_without_processing(
        self, from_env, ingest, service_cls
    ):
        from_env.return_value = object()
        ingest.return_value = {
            "source_id": "src_abc",
            "reused": False,
            "duration_ms": 1000,
            "source_kind": "LOCAL_VIDEO",
        }
        service_cls.return_value.start_analysis.return_value = {
            "data": {"error": {"code": "INVALID_SOURCE", "message": "bad source"}}
        }

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.cmd_analyze(self._args())

        self.assertEqual(code, 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["data"]["error"]["code"], "INVALID_SOURCE")


if __name__ == "__main__":
    unittest.main()
