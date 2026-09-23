"""The secret must never reach the preferences file (SEC-03).

All values here are obvious fakes.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vorquel_watch import credentials as cr
from vorquel_watch.cli import CONFIG_FILE_NAME, _write_preferences, purge_legacy_secret
from vorquel_watch.config import SECRET_ENV_VAR, _load_config_env, _resolve_secret


FAKE_SECRET = "sb_secret_THIS_IS_A_FAKE_TEST_VALUE_0123456789"
FAKE_URL = "https://example.invalid"


class PreferencesFileTests(unittest.TestCase):
    def test_preferences_file_never_contains_a_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "vorquel_watch.cli.default_data_dir", return_value=Path(tmp)
            ):
                path = _write_preferences(FAKE_URL)

            content = path.read_text(encoding="utf-8")
            self.assertIn(FAKE_URL, content)
            self.assertNotIn(SECRET_ENV_VAR, content)
            self.assertNotIn(FAKE_SECRET, content)

    def test_legacy_plaintext_secret_is_purged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CONFIG_FILE_NAME
            path.write_text(
                f"VORQUEL_WATCH_SUPABASE_URL={FAKE_URL}\n"
                f"{SECRET_ENV_VAR}={FAKE_SECRET}\n"
                "VORQUEL_WATCH_WHISPER_MODEL=small\n",
                encoding="utf-8",
            )

            self.assertTrue(purge_legacy_secret(path))

            content = path.read_text(encoding="utf-8")
            self.assertNotIn(FAKE_SECRET, content)
            self.assertNotIn(SECRET_ENV_VAR, content)
            self.assertIn(FAKE_URL, content)

            # Idempotent: nothing left to purge.
            self.assertFalse(purge_legacy_secret(path))


class SecretResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        os.environ.pop(SECRET_ENV_VAR, None)
        self.addCleanup(lambda: os.environ.pop(SECRET_ENV_VAR, None))

    def test_a_plaintext_secret_in_the_file_is_ignored(self) -> None:
        """An older install's secret must not keep the runtime working."""
        (self.data_dir / CONFIG_FILE_NAME).write_text(
            f"{SECRET_ENV_VAR}={FAKE_SECRET}\n"
            "VORQUEL_WATCH_WHISPER_MODEL=tiny\n",
            encoding="utf-8",
        )

        _load_config_env(self.data_dir)

        self.assertNotIn(SECRET_ENV_VAR, os.environ)
        # Non-secret preferences still load.
        self.assertEqual(os.environ.get("VORQUEL_WATCH_WHISPER_MODEL"), "tiny")
        os.environ.pop("VORQUEL_WATCH_WHISPER_MODEL", None)

    def test_environment_variable_takes_precedence(self) -> None:
        os.environ[SECRET_ENV_VAR] = FAKE_SECRET
        self.assertEqual(_resolve_secret(self.data_dir), FAKE_SECRET)

    def test_missing_credential_resolves_empty(self) -> None:
        self.assertEqual(_resolve_secret(self.data_dir), "")

    @unittest.skipUnless(cr.is_supported(), "DPAPI credential storage requires Windows")
    def test_secret_comes_from_the_credential_store(self) -> None:
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        self.assertEqual(_resolve_secret(self.data_dir), FAKE_SECRET)


if __name__ == "__main__":
    unittest.main()
