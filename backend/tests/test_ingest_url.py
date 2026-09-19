"""YouTube ingest through the control plane.

The network is never touched here. What is tested is the boundary: that a
refused URL costs nothing, that downloaded bytes go through the same Source
Guard as a local file, and that the working copy is not left behind.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vorquel_watch.cli import build_parser
from vorquel_watch.config import Settings
from vorquel_watch.ingest import ingest_youtube_url
from vorquel_watch.youtube import YouTubeError


VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

REMOTE = SimpleNamespace(
    video_id=VIDEO_ID,
    title="Workshop de automação",
    duration_s=3600,
    uploader="Vorquel",
    webpage_url=CANONICAL,
)


class IngestUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            data_dir=self.data_dir,
            supabase_url="https://example.invalid",
            supabase_secret_key="not-a-real-key",
        )
        self.workspace = (self.data_dir / "cache" / "youtube").resolve()

    def _download(self, *_args, **_kwargs):
        self.workspace.mkdir(parents=True, exist_ok=True)
        media = self.workspace / f"{VIDEO_ID}.mp4"
        media.write_bytes(b"pretend media")
        return media, REMOTE

    def test_a_refused_url_never_reaches_the_downloader(self) -> None:
        for url in [
            "http://www.youtube.com/watch?v=" + VIDEO_ID,
            "https://evil.example/watch?v=" + VIDEO_ID,
            "https://www.youtube.com/playlist?list=PLabcdefghijk",
            "file:///C:/Windows/System32",
        ]:
            with self.subTest(url=url):
                with mock.patch("vorquel_watch.youtube.download") as download:
                    with self.assertRaises(YouTubeError):
                        ingest_youtube_url(url, self.settings)
                download.assert_not_called()

    def test_downloaded_media_goes_through_source_guard(self) -> None:
        """Origin is metadata. It is never a reason to trust the bytes."""
        with mock.patch("vorquel_watch.youtube.download", side_effect=self._download):
            with mock.patch(
                "vorquel_watch.ingest._register",
                return_value={
                    "source_id": "src_abc",
                    "reused": False,
                    "duration_ms": 3600000,
                    "source_kind": "LOCAL_VIDEO",
                },
            ) as register:
                result = ingest_youtube_url(CANONICAL, self.settings)

        register.assert_called_once()
        metadata = register.call_args.kwargs["external_metadata"]
        self.assertEqual(metadata["origin"], "youtube")
        self.assertEqual(metadata["video_id"], VIDEO_ID)
        self.assertEqual(metadata["webpage_url"], CANONICAL)
        self.assertIn("Workshop", metadata["title"])
        self.assertEqual(result["origin"], "youtube")
        self.assertEqual(result["source_id"], "src_abc")

    def test_limits_come_from_settings(self) -> None:
        with mock.patch(
            "vorquel_watch.youtube.download", side_effect=self._download
        ) as download:
            with mock.patch("vorquel_watch.ingest._register", return_value={}):
                ingest_youtube_url(CANONICAL, self.settings)

        kwargs = download.call_args.kwargs
        self.assertEqual(kwargs["max_bytes"], self.settings.max_source_bytes)
        self.assertEqual(
            kwargs["max_duration_s"], self.settings.max_duration_ms // 1000
        )

    def test_the_working_copy_is_removed_after_success(self) -> None:
        with mock.patch("vorquel_watch.youtube.download", side_effect=self._download):
            with mock.patch("vorquel_watch.ingest._register", return_value={}):
                ingest_youtube_url(CANONICAL, self.settings)

        self.assertFalse(self.workspace.exists())

    def test_the_working_copy_is_removed_after_failure(self) -> None:
        """A failed ingest must not leave downloaded media on disk."""
        with mock.patch("vorquel_watch.youtube.download", side_effect=self._download):
            with mock.patch(
                "vorquel_watch.ingest._register",
                side_effect=ValueError("container format is not allowed"),
            ):
                with self.assertRaises(ValueError):
                    ingest_youtube_url(CANONICAL, self.settings)

        self.assertFalse(self.workspace.exists())


class CliSurfaceTests(unittest.TestCase):
    def test_ingest_url_is_reachable_from_the_cli(self) -> None:
        """Regression: the command existed but was never registered."""
        args = build_parser().parse_args(["ingest-url", CANONICAL])
        self.assertEqual(args.url, CANONICAL)
        self.assertTrue(callable(args.func))

    def test_no_mcp_style_arguments_leak_into_the_cli(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["ingest-url"])


if __name__ == "__main__":
    unittest.main()
