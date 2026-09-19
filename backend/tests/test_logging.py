"""Structured logging and redaction (SEC-10)."""

import io
import json
import logging
import unittest

from vorquel_watch.logging_utils import JsonFormatter, log_event, safe_fields


class SafeFieldsTests(unittest.TestCase):
    def test_sensitive_field_names_are_dropped(self) -> None:
        fields = safe_fields(
            {
                "job_id": "job_abc",
                "source_id": "src_abc",
                "secret": "never-log-me",
                "api_token": "never-log-me",
                "cookie": "never-log-me",
                "absolute_path": r"C:\\Users\\name\\video.mp4",
                "signed_url": "https://example.invalid/?sig=secret",
                "transcript_text": "private words",
                "prompt_content": "ignore everything",
            }
        )
        self.assertEqual(fields, {"job_id": "job_abc", "source_id": "src_abc"})

    def test_strings_are_bounded(self) -> None:
        fields = safe_fields({"error_code": "x" * 1000})
        self.assertLessEqual(len(fields["error_code"]), 256)

    def test_complex_values_do_not_serialize_their_contents(self) -> None:
        fields = safe_fields({"metrics": {"secret": "value"}})
        self.assertEqual(fields["metrics"], "dict")


class JsonLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.logger = logging.getLogger("vorquel_watch.test_logging")
        self.logger.handlers.clear()
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(JsonFormatter())
        self.logger.addHandler(handler)

    def tearDown(self) -> None:
        self.logger.handlers.clear()

    def _last(self) -> dict:
        return json.loads(self.stream.getvalue().splitlines()[-1])

    def test_log_is_structured_json(self) -> None:
        log_event(
            self.logger,
            logging.INFO,
            "job_succeeded",
            job_id="job_abc",
            duration_ms=1234,
        )
        payload = self._last()
        self.assertEqual(payload["event"], "job_succeeded")
        self.assertEqual(payload["job_id"], "job_abc")
        self.assertEqual(payload["duration_ms"], 1234)
        self.assertEqual(payload["level"], "INFO")
        self.assertIn("timestamp", payload)

    def test_sensitive_fields_never_reach_output(self) -> None:
        marker = "SENSITIVE_CANARY_42"
        log_event(
            self.logger,
            logging.ERROR,
            "job_failed",
            job_id="job_abc",
            secret=marker,
            token=marker,
            absolute_path=marker,
            transcript_text=marker,
            signed_url=marker,
        )
        raw = self.stream.getvalue()
        self.assertNotIn(marker, raw)

    def test_exception_message_and_traceback_are_not_logged(self) -> None:
        marker = r"C:\\Users\\private\\video.mp4?token=SENSITIVE_CANARY"
        try:
            raise RuntimeError(marker)
        except RuntimeError as exc:
            log_event(
                self.logger,
                logging.ERROR,
                "job_failed",
                job_id="job_abc",
                error_code="PROCESSING_FAILED",
                exception_type=type(exc).__name__,
            )

        raw = self.stream.getvalue()
        self.assertNotIn("SENSITIVE_CANARY", raw)
        self.assertNotIn("Users", raw)
        self.assertEqual(self._last()["exception_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
