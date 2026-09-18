from __future__ import annotations

from uuid import uuid4


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
}


def new_id(prefix: str) -> str:
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError("unsupported identifier prefix")
    return f"{prefix}{uuid4().hex}"
