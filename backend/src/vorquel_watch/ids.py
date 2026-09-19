from __future__ import annotations

import hashlib
import re
from uuid import uuid4


# Every prefix here has a matching CHECK constraint in the schema. The two must
# stay in sync: the database rejects an identifier whose prefix it does not
# recognise, and new_id refuses to mint one this set does not list.
_ALLOWED_PREFIXES = {
    "src_",
    "job_",
    "run_",
    "trn_",
    "seg_",
    "spk_",
    "turn_",
    "art_",
    "rev_",
    "req_",
    # Screen pipeline (ADR 0002).
    "obs_",
    "ocr_",
}

# Identifiers are opaque handles. Anything outside this alphabet - whitespace,
# slashes, backslashes, quotes, control characters, NUL - is rejected before it
# reaches the database layer, so a hostile value cannot be smuggled through an
# MCP argument that is later interpolated or logged.
_SUFFIX_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")

MAX_SUFFIX_LENGTH = 128


def new_id(prefix: str) -> str:
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError("unsupported identifier prefix")
    return f"{prefix}{uuid4().hex}"


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic opaque ID for idempotent persisted work.

    Only hashes of internal opaque identifiers/counters are emitted. This is
    used for retry-safe transcript segments; it does not encode host paths or
    media text in the identifier.
    """
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError("unsupported identifier prefix")
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}{hashlib.sha256(material).hexdigest()[:32]}"


def validate_id(value: object, prefix: str) -> str:
    """Return value if it is a well-formed identifier with prefix, else raise.

    SEC-08. Every identifier arriving from an MCP tool passes through here.
    The length is bounded first so an oversized payload is rejected before any
    pattern matching happens.
    """
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError("unsupported identifier prefix")
    if not isinstance(value, str):
        raise ValueError("identifier must be a string")
    if len(value) > len(prefix) + MAX_SUFFIX_LENGTH:
        raise ValueError("identifier is too long")
    if not value.startswith(prefix):
        raise ValueError("identifier has the wrong prefix")
    if not _SUFFIX_PATTERN.match(value[len(prefix) :]):
        raise ValueError("identifier contains unsupported characters")
    return value
