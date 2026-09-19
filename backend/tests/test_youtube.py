"""YouTube ingest boundary.

URL validation is the whole attack surface of the only component that reaches
the public network, and it is pure, so it is tested exhaustively here without
touching the network.
"""

import unittest
from unittest import mock

from vorquel_watch.youtube import (
    ALLOWED_HOSTS,
    YouTubeError,
    _child_env,
    canonical_video_url,
)


VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SECRET_VAR = "VORQUEL_WATCH_SUPABASE_SECRET_KEY"


class AcceptedUrlTests(unittest.TestCase):
    def test_watch_urls_normalise_to_canonical_form(self) -> None:
        for url in [
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/watch?v={VIDEO_ID}",
            f"https://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://music.youtube.com/watch?v={VIDEO_ID}",
        ]:
            with self.subTest(url=url):
                self.assertEqual(canonical_video_url(url), CANONICAL)

    def test_short_and_embed_forms_are_accepted(self) -> None:
        for url in [
            f"https://youtu.be/{VIDEO_ID}",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            f"https://www.youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube.com/live/{VIDEO_ID}",
        ]:
            with self.subTest(url=url):
                self.assertEqual(canonical_video_url(url), CANONICAL)

    def test_playlist_and_timestamp_parameters_are_dropped(self) -> None:
        """A playlist URL must not turn one ingest into hundreds."""
        noisy = (
            f"https://www.youtube.com/watch?v={VIDEO_ID}"
            "&list=PLabcdefghijklmnop&index=7&t=42s&pp=ygUK"
        )
        result = canonical_video_url(noisy)

        self.assertEqual(result, CANONICAL)
        self.assertNotIn("list=", result)
        self.assertNotIn("index=", result)
        self.assertNotIn("t=", result)

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        self.assertEqual(canonical_video_url(f"  {CANONICAL}  "), CANONICAL)


class RejectedUrlTests(unittest.TestCase):
    def _assert_refused(self, url) -> None:
        with self.assertRaises(YouTubeError):
            canonical_video_url(url)

    def test_non_https_schemes_are_refused(self) -> None:
        for url in [
            f"http://www.youtube.com/watch?v={VIDEO_ID}",
            f"ftp://www.youtube.com/watch?v={VIDEO_ID}",
            "file:///C:/Windows/System32/config/SAM",
            "javascript:alert(1)",
            "data:text/html,<script>",
            f"//www.youtube.com/watch?v={VIDEO_ID}",
        ]:
            with self.subTest(url=url):
                self._assert_refused(url)

    def test_lookalike_hosts_are_refused(self) -> None:
        """An allowlist, so a new lookalike domain does not silently work."""
        for host in [
            "youtube.com.evil.example",
            "evil.example",
            "notyoutube.com",
            "youtube.evil.example",
            "www.yout-ube.com",
            "localhost",
            "127.0.0.1",
            "169.254.169.254",
            "[::1]",
        ]:
            with self.subTest(host=host):
                self._assert_refused(f"https://{host}/watch?v={VIDEO_ID}")

    def test_urls_that_do_not_name_one_video_are_refused(self) -> None:
        for url in [
            "https://www.youtube.com/",
            "https://www.youtube.com/feed/subscriptions",
            "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
            "https://www.youtube.com/@somechannel",
            "https://www.youtube.com/results?search_query=test",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/watch?v=",
        ]:
            with self.subTest(url=url):
                self._assert_refused(url)

    def test_malformed_video_ids_are_refused(self) -> None:
        for bad in ["short", "waytoolongvideoid", "bad/../id", "id with space", "a" * 200]:
            with self.subTest(video_id=bad):
                self._assert_refused(f"https://www.youtube.com/watch?v={bad}")

    def test_non_string_and_oversized_input_is_refused(self) -> None:
        for value in [None, 12345, b"https://youtu.be/x", ["url"], "https://" + "a" * 4000]:
            with self.subTest(value=type(value).__name__):
                self._assert_refused(value)

    def test_refusal_never_echoes_the_supplied_url(self) -> None:
        hostile = "https://evil.example/watch?v=" + "A" * 11
        with self.assertRaises(YouTubeError) as ctx:
            canonical_video_url(hostile)
        self.assertNotIn(hostile, str(ctx.exception))
        self.assertNotIn("evil.example", str(ctx.exception))


class HostAllowlistTests(unittest.TestCase):
    def test_allowlist_contains_only_youtube_hosts(self) -> None:
        for host in ALLOWED_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(
                    host.endswith("youtube.com") or host.endswith("youtu.be"),
                    f"unexpected host in allowlist: {host}",
                )


class ChildEnvironmentTests(unittest.TestCase):
    def test_the_downloader_never_receives_the_credential(self) -> None:
        with mock.patch.dict("os.environ", {SECRET_VAR: "CANARY", "HF_TOKEN": "x"}):
            env = _child_env()

        self.assertNotIn(SECRET_VAR, env)
        self.assertNotIn("HF_TOKEN", env)
        self.assertNotIn("CANARY", "".join(env.values()))


if __name__ == "__main__":
    unittest.main()
