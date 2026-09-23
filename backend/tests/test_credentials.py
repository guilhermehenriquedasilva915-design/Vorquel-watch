"""OS-backed credential storage (SEC-03).

Every value used here is an obvious fake. No real credential is involved.
"""

import tempfile
import unittest
from pathlib import Path

from vorquel_watch import credentials as cr


FAKE_SECRET = "sb_secret_THIS_IS_A_FAKE_TEST_VALUE_0123456789"


@unittest.skipUnless(cr.is_supported(), "DPAPI credential storage requires Windows")
class DpapiCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_round_trip(self) -> None:
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        self.assertEqual(
            cr.load_secret(self.data_dir, cr.SUPABASE_SECRET_NAME), FAKE_SECRET
        )

    def test_stored_blob_is_not_plaintext(self) -> None:
        path = cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        blob = path.read_bytes()

        self.assertNotIn(FAKE_SECRET.encode("utf-8"), blob)
        # DPAPI blobs start with a version word and a well-known provider GUID.
        self.assertTrue(blob.startswith(b"\x01\x00\x00\x00"))

    def test_missing_secret_reads_as_none(self) -> None:
        self.assertIsNone(cr.load_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))
        self.assertFalse(cr.has_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))

    def test_delete_is_idempotent_and_reported(self) -> None:
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        self.assertTrue(cr.has_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))

        self.assertTrue(cr.delete_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))
        self.assertFalse(cr.delete_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))
        self.assertIsNone(cr.load_secret(self.data_dir, cr.SUPABASE_SECRET_NAME))

    def test_rotation_replaces_the_previous_value(self) -> None:
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        rotated = FAKE_SECRET + "_ROTATED"
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, rotated)

        self.assertEqual(
            cr.load_secret(self.data_dir, cr.SUPABASE_SECRET_NAME), rotated
        )
        blob = cr._credential_path(self.data_dir, cr.SUPABASE_SECRET_NAME).read_bytes()
        self.assertNotIn(FAKE_SECRET.encode("utf-8"), blob)

    def test_tampered_blob_is_rejected(self) -> None:
        cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, FAKE_SECRET)
        path = cr._credential_path(self.data_dir, cr.SUPABASE_SECRET_NAME)

        raw = bytearray(path.read_bytes())
        raw[-1] ^= 0xFF
        path.write_bytes(bytes(raw))

        with self.assertRaises(cr.CredentialError) as ctx:
            cr.load_secret(self.data_dir, cr.SUPABASE_SECRET_NAME)
        self.assertNotIn(FAKE_SECRET, str(ctx.exception))

    def test_empty_secret_is_refused(self) -> None:
        with self.assertRaises(cr.CredentialError):
            cr.store_secret(self.data_dir, cr.SUPABASE_SECRET_NAME, "")

    def test_credential_name_cannot_escape_the_directory(self) -> None:
        for name in ["../escape", "a/b", "a\\b", "", "with space", ".", ".."]:
            with self.subTest(name=repr(name)):
                with self.assertRaises(cr.CredentialError):
                    cr._credential_path(self.data_dir, name)


class UnsupportedPlatformTests(unittest.TestCase):
    def test_non_windows_fails_closed(self) -> None:
        """No silent plaintext fallback: storage refuses where DPAPI is absent."""
        if cr.is_supported():
            self.skipTest("this host has DPAPI")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cr.CredentialError):
                cr.store_secret(Path(tmp), cr.SUPABASE_SECRET_NAME, FAKE_SECRET)


if __name__ == "__main__":
    unittest.main()
