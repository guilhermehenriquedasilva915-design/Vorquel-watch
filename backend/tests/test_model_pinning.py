"""Model supply chain (SEC-05).

A logical model name is not an identity: faster-whisper resolves "small" to a
HuggingFace repository whose contents can change. These tests cover the pin
resolution and the provenance it produces.
"""

import unittest
from pathlib import Path

from vorquel_watch.config import (
    PINNED_MODEL_REVISIONS,
    Settings,
    resolve_model_pin,
)
from vorquel_watch.transcription import FasterWhisperEngine


COMMIT_LENGTH = 40


def _settings(**overrides: object) -> Settings:
    base = {
        "data_dir": Path("."),
        "supabase_url": "https://example.invalid",
        "supabase_secret_key": "not-a-real-key",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class ModelPinResolutionTests(unittest.TestCase):
    def test_every_shipped_pin_is_a_full_commit_hash(self) -> None:
        self.assertTrue(PINNED_MODEL_REVISIONS)
        for name, (repository, revision) in PINNED_MODEL_REVISIONS.items():
            with self.subTest(model=name):
                self.assertIn("/", repository)
                self.assertEqual(len(revision), COMMIT_LENGTH)
                self.assertRegex(revision, r"\A[0-9a-f]{40}\Z")

    def test_known_model_resolves_to_its_pin(self) -> None:
        repository, revision = resolve_model_pin("small", None)
        self.assertEqual(repository, "Systran/faster-whisper-small")
        self.assertEqual(revision, PINNED_MODEL_REVISIONS["small"][1])

    def test_unknown_model_without_a_revision_fails_closed(self) -> None:
        """No silent download at whatever HEAD happens to be."""
        with self.assertRaises(RuntimeError) as ctx:
            resolve_model_pin("large-v3", None)
        self.assertIn("pinned revision", str(ctx.exception))

    def test_unknown_model_with_an_explicit_revision_is_allowed(self) -> None:
        revision = "a" * COMMIT_LENGTH
        repository, resolved = resolve_model_pin("some-org/custom-model", revision)
        self.assertEqual(repository, "some-org/custom-model")
        self.assertEqual(resolved, revision)

    def test_explicit_revision_overrides_the_shipped_pin(self) -> None:
        revision = "b" * COMMIT_LENGTH
        repository, resolved = resolve_model_pin("small", revision)
        self.assertEqual(repository, "Systran/faster-whisper-small")
        self.assertEqual(resolved, revision)

    def test_a_malformed_revision_is_rejected(self) -> None:
        for revision in ["abc", "z" * COMMIT_LENGTH, "a" * 39, "a" * 41, "main", "HEAD"]:
            with self.subTest(revision=revision):
                with self.assertRaises(RuntimeError):
                    resolve_model_pin("small", revision)

    def test_empty_model_is_rejected(self) -> None:
        for model in ["", "   ", None]:
            with self.subTest(model=model):
                with self.assertRaises(RuntimeError):
                    resolve_model_pin(model, None)  # type: ignore[arg-type]


class EngineDescriptorTests(unittest.TestCase):
    def test_descriptor_records_the_exact_model_identity(self) -> None:
        engine = FasterWhisperEngine(_settings())
        descriptor = engine.engine_descriptor()

        self.assertEqual(descriptor["model"], "small")
        self.assertEqual(descriptor["model_repository"], "Systran/faster-whisper-small")
        self.assertEqual(
            descriptor["model_revision"], PINNED_MODEL_REVISIONS["small"][1]
        )
        self.assertEqual(descriptor["runtime"], "ctranslate2")
        self.assertEqual(descriptor["device_class"], "CPU")
        self.assertEqual(descriptor["compute_type"], "int8")

    def test_descriptor_records_engine_and_runtime_versions(self) -> None:
        """A logical name plus a version is not enough to reproduce a run."""
        descriptor = FasterWhisperEngine(_settings()).engine_descriptor()

        for field in ("version", "runtime_version"):
            with self.subTest(field=field):
                self.assertNotEqual(descriptor[field], "unknown")
                self.assertRegex(descriptor[field], r"\A\d+\.\d+")

    def test_unpinned_model_refuses_before_any_download(self) -> None:
        engine = FasterWhisperEngine(_settings(whisper_model="large-v3"))
        with self.assertRaises(RuntimeError):
            engine.engine_descriptor()
        with self.assertRaises(RuntimeError):
            engine.model_pin()


if __name__ == "__main__":
    unittest.main()
