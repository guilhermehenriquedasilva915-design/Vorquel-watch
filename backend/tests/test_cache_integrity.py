"""Content-addressed cache integrity (SEC-04).

A hit in content-addressed storage proves only that a file sits under that
name. These tests cover what happens when the bytes no longer match it.
"""

import json
import tempfile
import unittest
from pathlib import Path

from vorquel_watch.local_storage import IntegrityError, LocalStorage, sha256_file


GENUINE = b"genuine vorquel watch evidence payload"
TAMPERED = b"tampered payload of a different length entirely"


class CacheIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.source = root / "input.bin"
        self.source.write_bytes(GENUINE)
        self.digest = sha256_file(self.source)
        self.storage = LocalStorage(root / "data")
        self.storage.ensure()

    def _corrupt_stored_object(self) -> None:
        self.storage.object_path(self.digest).write_bytes(TAMPERED)

    def _incidents(self) -> list[dict]:
        log = self.storage.logs / "integrity-incidents.jsonl"
        if not log.is_file():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_clean_reuse_does_not_quarantine(self) -> None:
        first = self.storage.store_source(self.source, self.digest)
        second = self.storage.store_source(self.source, self.digest)

        self.assertEqual(first, second)
        self.assertEqual(sha256_file(second), self.digest)
        self.assertEqual(self._incidents(), [])
        self.assertEqual(list(self.storage.quarantine.iterdir()), [])

    def test_corrupted_object_is_not_reused(self) -> None:
        """Regression: store_source returned a cache hit without reading it."""
        self.storage.store_source(self.source, self.digest)
        self._corrupt_stored_object()

        restored = self.storage.store_source(self.source, self.digest)

        self.assertEqual(sha256_file(restored), self.digest)
        self.assertEqual(restored.read_bytes(), GENUINE)

    def test_corruption_is_quarantined_not_overwritten(self) -> None:
        self.storage.store_source(self.source, self.digest)
        self._corrupt_stored_object()
        self.storage.store_source(self.source, self.digest)

        quarantined = list(self.storage.quarantine.iterdir())
        self.assertEqual(len(quarantined), 1)
        # The bad bytes are preserved for inspection, never silently destroyed.
        self.assertEqual(quarantined[0].read_bytes(), TAMPERED)

    def test_incident_is_recorded_with_both_digests(self) -> None:
        self.storage.store_source(self.source, self.digest)
        self._corrupt_stored_object()
        self.storage.store_source(self.source, self.digest)

        incidents = self._incidents()
        self.assertEqual(len(incidents), 1)
        entry = incidents[0]
        self.assertEqual(entry["event"], "cache_quarantine")
        self.assertEqual(entry["expected_sha256"], self.digest)
        self.assertEqual(entry["actual_sha256"], sha256_file(
            self.storage.quarantine / entry["quarantined_as"]
        ))
        self.assertIn("recorded_at", entry)

    def test_incident_record_carries_no_host_path_or_content(self) -> None:
        self.storage.store_source(self.source, self.digest)
        self._corrupt_stored_object()
        self.storage.store_source(self.source, self.digest)

        raw = (self.storage.logs / "integrity-incidents.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(str(self.storage.root), raw)
        self.assertNotIn(str(self.source), raw)
        self.assertNotIn(TAMPERED.decode(), raw)

    def test_verify_object_accepts_an_intact_object(self) -> None:
        stored = self.storage.store_source(self.source, self.digest)
        self.assertEqual(self.storage.verify_object(self.digest), stored)

    def test_verify_object_rejects_and_quarantines_corruption(self) -> None:
        self.storage.store_source(self.source, self.digest)
        self._corrupt_stored_object()

        with self.assertRaises(IntegrityError):
            self.storage.verify_object(self.digest)

        self.assertEqual(len(list(self.storage.quarantine.iterdir())), 1)
        self.assertEqual(len(self._incidents()), 1)

    def test_verify_object_rejects_a_missing_object(self) -> None:
        with self.assertRaises(IntegrityError):
            self.storage.verify_object(self.digest)

    def test_verify_object_rejects_a_malformed_digest(self) -> None:
        for bad in ["", "zz", "g" * 64, "../../etc/passwd", self.digest.upper()]:
            with self.subTest(digest=bad):
                with self.assertRaises(ValueError):
                    self.storage.verify_object(bad)


if __name__ == "__main__":
    unittest.main()
