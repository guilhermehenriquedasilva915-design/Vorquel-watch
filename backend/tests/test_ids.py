"""Identifier validation at the MCP boundary (SEC-08)."""

import unittest

from vorquel_watch.ids import MAX_SUFFIX_LENGTH, new_id, validate_id


class ValidateIdTests(unittest.TestCase):
    def test_accepts_a_generated_id(self) -> None:
        for prefix in ("src_", "job_", "trn_", "seg_", "spk_", "turn_", "art_"):
            with self.subTest(prefix=prefix):
                value = new_id(prefix)
                self.assertEqual(validate_id(value, prefix), value)

    def test_rejects_wrong_prefix(self) -> None:
        value = new_id("job_")
        with self.assertRaises(ValueError):
            validate_id(value, "src_")

    def test_rejects_unsupported_prefix(self) -> None:
        with self.assertRaises(ValueError):
            validate_id("path_etc_passwd", "path_")

    def test_rejects_non_string(self) -> None:
        for value in (None, 12345, ["src_abc"], {"src_": "abc"}, b"src_abc"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    validate_id(value, "src_")

    def test_rejects_oversized_input(self) -> None:
        with self.assertRaises(ValueError):
            validate_id("src_" + "a" * (MAX_SUFFIX_LENGTH + 1), "src_")

    def test_rejects_empty_suffix(self) -> None:
        with self.assertRaises(ValueError):
            validate_id("src_", "src_")

    def test_rejects_hostile_characters(self) -> None:
        hostile = [
            "src_../../etc/passwd",
            "src_..\\..\\windows\\system32",
            "src_abc def",
            "src_abc\tdef",
            "src_abc\ndef",
            "src_abc\x00def",
            "src_abc'; drop table sources;--",
            "src_abc%20def",
            "src_abc/def",
            "src_abc\\def",
            "src_abc.def",
            "src_abc*",
            "src_<script>",
            "src_abc‮en",
            " src_abc",
            "src_abc ",
        ]
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    validate_id(value, "src_")

    def test_rejection_message_does_not_echo_input(self) -> None:
        """An error string is reflected back to the caller; it must not carry
        the attacker-supplied value."""
        payload = "src_abc'; drop table sources;--"
        with self.assertRaises(ValueError) as ctx:
            validate_id(payload, "src_")
        self.assertNotIn(payload, str(ctx.exception))
        self.assertNotIn("drop table", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
