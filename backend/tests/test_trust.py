import unittest

from vorquel_watch.contracts import DataTrustClass, InstructionAuthority
from vorquel_watch.security import security_for_media_payload


class TrustBoundaryTests(unittest.TestCase):
    def test_external_media_has_no_instruction_authority(self) -> None:
        security = security_for_media_payload(DataTrustClass.UNTRUSTED_MEDIA)
        self.assertEqual(security.instruction_authority, InstructionAuthority.NONE)

    def test_derived_media_has_no_instruction_authority(self) -> None:
        security = security_for_media_payload(DataTrustClass.UNTRUSTED_DERIVED)
        self.assertEqual(security.instruction_authority, InstructionAuthority.NONE)

    def test_human_authored_correction_still_has_no_instruction_authority(self) -> None:
        security = security_for_media_payload(DataTrustClass.USER_AUTHORED_DATA)
        self.assertEqual(security.instruction_authority, InstructionAuthority.NONE)

    def test_server_control_cannot_be_created_as_media_payload(self) -> None:
        with self.assertRaises(ValueError):
            security_for_media_payload(DataTrustClass.SERVER_CONTROL)


if __name__ == "__main__":
    unittest.main()
