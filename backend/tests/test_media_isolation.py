"""Out-of-process media parsing (SEC-02).

The parse of attacker-controlled bytes must not happen in the worker or MCP
process, must not inherit the parent's environment, and must not return
anything but sanitized data.
"""

import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from vorquel_watch import media_probe
from vorquel_watch.source_guard import _CHILD_ENV_PASSTHROUGH, _child_env, probe_media


SECRET_VAR = "VORQUEL_WATCH_SUPABASE_SECRET_KEY"
CANARY_VALUE = "CANARY_SECRET_DO_NOT_LEAK"


def _write_wav(path: Path, seconds: int = 2, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * t / rate)))
                for t in range(rate * seconds)
            )
        )


class ChildEnvironmentTests(unittest.TestCase):
    def test_secret_is_never_passed_to_the_child(self) -> None:
        with mock.patch.dict("os.environ", {SECRET_VAR: CANARY_VALUE}):
            env = _child_env()

        self.assertNotIn(SECRET_VAR, env)
        self.assertNotIn(CANARY_VALUE, "".join(env.values()))

    def test_only_allowlisted_variables_are_forwarded(self) -> None:
        extra = {"CANARY_MARKER": "x", "AWS_SECRET_ACCESS_KEY": "y", "HF_TOKEN": "z"}
        with mock.patch.dict("os.environ", extra):
            env = _child_env()

        permitted = set(_CHILD_ENV_PASSTHROUGH) | {
            "PYTHONNOUSERSITE",
            "PYTHONDONTWRITEBYTECODE",
        }
        self.assertTrue(set(env).issubset(permitted), f"unexpected: {set(env) - permitted}")

    def test_a_real_child_process_cannot_read_the_secret(self) -> None:
        """Not just absent from the dict: absent from the spawned process."""
        with mock.patch.dict("os.environ", {SECRET_VAR: CANARY_VALUE}):
            env = _child_env()

        completed = subprocess.run(
            [sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
            capture_output=True,
            env=env,
            text=True,
            check=True,
        )
        self.assertNotIn(SECRET_VAR, json.loads(completed.stdout))


class ParseHappensOutOfProcessTests(unittest.TestCase):
    def test_parent_process_never_imports_the_media_stack(self) -> None:
        """If av were imported here, hostile bytes would be parsed in-process."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            _write_wav(wav)

            program = (
                "import sys\n"
                "from vorquel_watch.source_guard import probe_media\n"
                f"probe_media(__import__('pathlib').Path(r'{wav}'))\n"
                "print('av' in sys.modules)\n"
            )
            completed = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(completed.stdout.strip(), "False")


class ChildFailureHandlingTests(unittest.TestCase):
    """The parent must treat every child outcome as untrusted."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "tone.wav"
        _write_wav(self.path)
        self.addCleanup(self._tmp.cleanup)

    def _with_child_result(self, **kwargs):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        for key, value in kwargs.items():
            setattr(completed, key, value)
        return mock.patch("subprocess.run", return_value=completed)

    def test_timeout_is_a_rejection(self) -> None:
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1),
        ):
            with self.assertRaises(ValueError) as ctx:
                probe_media(self.path)
        self.assertIn("time limit", str(ctx.exception))

    def test_child_crash_is_a_rejection(self) -> None:
        with self._with_child_result(returncode=1, stderr=b"ffmpeg said something"):
            with self.assertRaises(ValueError) as ctx:
                probe_media(self.path)
        self.assertNotIn("ffmpeg", str(ctx.exception).lower())

    def test_malformed_child_output_is_a_rejection(self) -> None:
        with self._with_child_result(stdout=b"not json at all"):
            with self.assertRaises(ValueError):
                probe_media(self.path)

    def test_non_object_child_output_is_a_rejection(self) -> None:
        with self._with_child_result(stdout=b'["unexpected"]'):
            with self.assertRaises(ValueError):
                probe_media(self.path)

    def test_oversized_child_output_is_a_rejection(self) -> None:
        with self._with_child_result(stdout=b"{}" + b"x" * (64 * 1024 + 1)):
            with self.assertRaises(ValueError) as ctx:
                probe_media(self.path)
        self.assertIn("too much output", str(ctx.exception))

    def test_stream_list_of_the_wrong_shape_is_a_rejection(self) -> None:
        payload = json.dumps(
            {"ok": True, "format": "wav", "duration_raw": 1, "time_base": 1, "streams": "nope"}
        ).encode()
        with self._with_child_result(stdout=payload):
            with self.assertRaises(ValueError):
                probe_media(self.path)


class ChildProtocolTests(unittest.TestCase):
    def test_bad_invocation_returns_json_not_a_traceback(self) -> None:
        for argv in ([], ["a", "b"]):
            with self.subTest(argv=argv):
                with mock.patch("sys.stdout.write") as write:
                    self.assertEqual(media_probe.main(argv), 0)
                payload = json.loads(write.call_args[0][0])
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"], "bad_invocation")

    def test_child_never_returns_the_path_or_ffmpeg_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "payload.mp4"
            bogus.write_bytes(b"not media at all" * 200)

            payload = media_probe.probe(str(bogus))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "undecodable_media")
        self.assertNotIn(str(bogus), json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
