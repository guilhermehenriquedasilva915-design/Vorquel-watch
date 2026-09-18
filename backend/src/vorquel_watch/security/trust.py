from dataclasses import dataclass

from vorquel_watch.contracts import DataTrustClass, InstructionAuthority


@dataclass(frozen=True, slots=True)
class ContentSecurity:
    data_trust_class: DataTrustClass
    instruction_authority: InstructionAuthority


def security_for_media_payload(
    trust_class: DataTrustClass,
) -> ContentSecurity:
    """Return the mandatory security envelope for media-originated data."""
    if trust_class is DataTrustClass.SERVER_CONTROL:
        raise ValueError("SERVER_CONTROL is not a media payload trust class")

    return ContentSecurity(
        data_trust_class=trust_class,
        instruction_authority=InstructionAuthority.NONE,
    )
