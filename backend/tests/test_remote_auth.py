"""The remote API's authentication boundary.

The API is the only network surface the Watch exposes, and it sits in front of
ingest. These tests pin the properties that make it safe to expose at all: it
refuses to run without a credential, it refuses a guessable one, and it does not
accept anything that merely resembles the real token.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vorquel_watch import remote_auth


GOOD_TOKEN = "t" * remote_auth.MIN_TOKEN_LENGTH


class ServerTokenTests(unittest.TestCase):
    def test_missing_token_refuses_to_start(self) -> None:
        """No credential must mean no server.

        Starting without a token would accept every caller on the host, which is
        strictly worse than failing to boot.
        """
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(remote_auth.TokenNotConfigured):
                remote_auth.load_server_token()

    def test_short_token_is_refused(self) -> None:
        with mock.patch.dict(
            os.environ, {remote_auth.TOKEN_ENV_VAR: "short"}, clear=True
        ):
            with self.assertRaises(remote_auth.TokenNotConfigured):
                remote_auth.load_server_token()

    def test_token_is_read_from_environment(self) -> None:
        with mock.patch.dict(
            os.environ, {remote_auth.TOKEN_ENV_VAR: GOOD_TOKEN}, clear=True
        ):
            self.assertEqual(remote_auth.load_server_token(), GOOD_TOKEN)

    def test_token_file_is_stripped_of_trailing_newline(self) -> None:
        """A systemd operator writes the token with echo, which appends a newline.

        Treating that newline as part of the secret would make every request fail
        for a reason no one would guess.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token"
            path.write_text(GOOD_TOKEN + "\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {remote_auth.TOKEN_FILE_ENV_VAR: str(path)},
                clear=True,
            ):
                self.assertEqual(remote_auth.load_server_token(), GOOD_TOKEN)

    def test_unreadable_token_file_refuses_and_does_not_echo_the_path(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "vorquel-absent-token-file")
        with mock.patch.dict(
            os.environ, {remote_auth.TOKEN_FILE_ENV_VAR: missing}, clear=True
        ):
            with self.assertRaises(remote_auth.TokenNotConfigured) as caught:
                remote_auth.load_server_token()
        self.assertNotIn(missing, str(caught.exception))

    def test_environment_wins_over_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token"
            path.write_text("f" * remote_auth.MIN_TOKEN_LENGTH, encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    remote_auth.TOKEN_ENV_VAR: GOOD_TOKEN,
                    remote_auth.TOKEN_FILE_ENV_VAR: str(path),
                },
                clear=True,
            ):
                self.assertEqual(remote_auth.load_server_token(), GOOD_TOKEN)


class HeaderParsingTests(unittest.TestCase):
    def test_bearer_scheme_is_accepted_case_insensitively(self) -> None:
        for header in (f"Bearer {GOOD_TOKEN}", f"bearer {GOOD_TOKEN}", f"BEARER {GOOD_TOKEN}"):
            self.assertEqual(remote_auth.token_from_header(header), GOOD_TOKEN)

    def test_malformed_headers_yield_no_credential(self) -> None:
        """A hostile header shape is an ordinary failure, not a distinct outcome.

        Each of these must be indistinguishable from "wrong token" to the caller.
        """
        for header in (
            None,
            "",
            "   ",
            GOOD_TOKEN,
            f"Basic {GOOD_TOKEN}",
            "Bearer",
            f"Token {GOOD_TOKEN}",
        ):
            self.assertEqual(remote_auth.token_from_header(header), "")

    def test_authorization_is_rejected_for_wrong_and_empty_tokens(self) -> None:
        self.assertFalse(remote_auth.is_authorized(f"Bearer {'x' * 32}", GOOD_TOKEN))
        self.assertFalse(remote_auth.is_authorized(None, GOOD_TOKEN))
        self.assertFalse(remote_auth.is_authorized(f"Bearer {GOOD_TOKEN}", ""))

    def test_a_prefix_of_the_real_token_is_not_accepted(self) -> None:
        """Guards against a length-insensitive or prefix comparison."""
        self.assertFalse(
            remote_auth.is_authorized(f"Bearer {GOOD_TOKEN[:-1]}", GOOD_TOKEN)
        )
        self.assertFalse(
            remote_auth.is_authorized(f"Bearer {GOOD_TOKEN}x", GOOD_TOKEN)
        )

    def test_correct_token_is_accepted(self) -> None:
        self.assertTrue(remote_auth.is_authorized(f"Bearer {GOOD_TOKEN}", GOOD_TOKEN))


if __name__ == "__main__":
    unittest.main()
