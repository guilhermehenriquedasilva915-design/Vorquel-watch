from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from vorquel_watch.ids import new_id, validate_id


KNOWLEDGE_TYPES = {
    "CONCEPT",
    "PROCEDURE",
    "PATTERN",
    "ANTI_PATTERN",
    "ERROR",
    "FIX",
    "EXAMPLE",
    "CLAIM",
    "TOOL_USAGE",
    "CHECKLIST",
    "WARNING",
}

EPISTEMIC_STATUSES = {
    "OBSERVADO",
    "DECLARADO",
    "MEDIDO",
    "INFERIDO",
    "HIPOTESE",
    "ESTIMADO",
    "DESCONHECIDO",
    "CONFLITANTE",
    "INVALIDADO",
}

CANDIDATE_STATUSES = {"PENDING", "APPROVED", "REJECTED"}


@dataclass(frozen=True)
class KnowledgeProvenance:
    start_ms: int | None = None
    end_ms: int | None = None
    transcript_id: str | None = None
    segment_id: str | None = None
    screen_observation_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        if self.start_ms is not None and int(self.start_ms) < 0:
            raise ValueError("start_ms must not be negative")
        if self.end_ms is not None and int(self.end_ms) < 0:
            raise ValueError("end_ms must not be negative")
        if (
            self.start_ms is not None
            and self.end_ms is not None
            and int(self.end_ms) < int(self.start_ms)
        ):
            raise ValueError("end_ms must be greater than or equal to start_ms")

        payload: dict[str, Any] = {
            "knowledge_source_id": new_id("ksr_"),
            "start_ms": int(self.start_ms) if self.start_ms is not None else None,
            "end_ms": int(self.end_ms) if self.end_ms is not None else None,
        }
        if self.transcript_id is not None:
            payload["transcript_id"] = validate_id(self.transcript_id, "trn_")
        if self.segment_id is not None:
            payload["segment_id"] = validate_id(self.segment_id, "seg_")
        if self.screen_observation_id is not None:
            payload["screen_observation_id"] = validate_id(
                self.screen_observation_id, "obs_"
            )
        return payload


def _bounded_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} is required")
    if len(clean) > maximum:
        raise ValueError(f"{field} is too long")
    return clean


def canonical_knowledge_hash(
    *,
    source_id: str,
    knowledge_type: str,
    domain: str,
    title: str,
    summary: str,
    epistemic_status: str,
) -> str:
    payload = {
        "source_id": source_id,
        "knowledge_type": knowledge_type,
        "domain": domain,
        "title": title,
        "summary": summary,
        "epistemic_status": epistemic_status,
        "schema_version": "1.0",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class KnowledgeWriter:
    """Controlled application boundary for persistent learning.

    The writer never grants instruction authority. Approval means the human
    accepted an item as reusable reference material, not that media-derived
    text can choose tools or modify policy.
    """

    def __init__(self, repo: Any) -> None:
        self.repo = repo

    def propose(
        self,
        *,
        source_id: str,
        knowledge_type: str,
        domain: str,
        title: str,
        summary: str,
        epistemic_status: str,
        provenance: list[KnowledgeProvenance] | None = None,
    ) -> dict[str, Any]:
        source_id = validate_id(source_id, "src_")
        normalized_type = (
            knowledge_type.strip().upper() if isinstance(knowledge_type, str) else ""
        )
        normalized_epistemic = (
            epistemic_status.strip().upper()
            if isinstance(epistemic_status, str)
            else ""
        )
        if normalized_type not in KNOWLEDGE_TYPES:
            raise ValueError("unsupported knowledge_type")
        if normalized_epistemic not in EPISTEMIC_STATUSES:
            raise ValueError("unsupported epistemic_status")

        clean_domain = _bounded_text(domain, field="domain", maximum=120)
        clean_title = _bounded_text(title, field="title", maximum=300)
        clean_summary = _bounded_text(summary, field="summary", maximum=12000)

        rows = provenance or []
        if len(rows) > 100:
            raise ValueError("too many provenance rows")
        provenance_payload = [row.as_payload() for row in rows]

        content_hash = canonical_knowledge_hash(
            source_id=source_id,
            knowledge_type=normalized_type,
            domain=clean_domain,
            title=clean_title,
            summary=clean_summary,
            epistemic_status=normalized_epistemic,
        )

        return self.repo.create_knowledge_candidate(
            candidate_id=new_id("knd_"),
            source_id=source_id,
            knowledge_type=normalized_type,
            domain=clean_domain,
            title=clean_title,
            summary=clean_summary,
            epistemic_status=normalized_epistemic,
            content_hash=content_hash,
            provenance=provenance_payload,
        )

    def approve(self, candidate_id: str, note: str | None = None) -> dict[str, Any]:
        candidate_id = validate_id(candidate_id, "knd_")
        clean_note = None
        if note is not None:
            clean_note = _bounded_text(note, field="note", maximum=4000)
        return self.repo.approve_knowledge_candidate(
            candidate_id=candidate_id,
            review_id=new_id("krv_"),
            knowledge_id=new_id("knw_"),
            note=clean_note,
        )

    def reject(self, candidate_id: str, note: str | None = None) -> dict[str, Any]:
        candidate_id = validate_id(candidate_id, "knd_")
        clean_note = None
        if note is not None:
            clean_note = _bounded_text(note, field="note", maximum=4000)
        return self.repo.reject_knowledge_candidate(
            candidate_id=candidate_id,
            review_id=new_id("krv_"),
            note=clean_note,
        )

    def list_candidates(
        self,
        *,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if source_id is not None:
            source_id = validate_id(source_id, "src_")
        normalized_status = None
        if status is not None:
            normalized_status = status.strip().upper()
            if normalized_status not in CANDIDATE_STATUSES:
                raise ValueError("unsupported candidate status")
        return self.repo.list_knowledge_candidates(
            source_id=source_id,
            status=normalized_status,
            limit=max(1, min(int(limit), 100)),
        )

    def withdraw(self, knowledge_id: str, *, reason: str) -> dict[str, Any]:
        knowledge_id = validate_id(knowledge_id, "knw_")
        clean_reason = _bounded_text(reason, field="reason", maximum=4000)
        return self.repo.withdraw_knowledge_item(
            knowledge_id=knowledge_id,
            event_id=new_id("kev_"),
            reason=clean_reason,
        )

    def supersede(
        self,
        knowledge_id: str,
        replacement_knowledge_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        knowledge_id = validate_id(knowledge_id, "knw_")
        replacement_knowledge_id = validate_id(replacement_knowledge_id, "knw_")
        if knowledge_id == replacement_knowledge_id:
            raise ValueError("knowledge item cannot supersede itself")
        clean_reason = _bounded_text(reason, field="reason", maximum=4000)
        return self.repo.supersede_knowledge_item(
            knowledge_id=knowledge_id,
            replacement_knowledge_id=replacement_knowledge_id,
            event_id=new_id("kev_"),
            reason=clean_reason,
        )

    def synthesize(
        self,
        query: str,
        *,
        domain: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        clean_query = _bounded_text(query, field="query", maximum=500)
        clean_domain = None
        if domain is not None:
            clean_domain = _bounded_text(domain, field="domain", maximum=120)
        return self.repo.synthesize_knowledge_context(
            query=clean_query,
            domain=clean_domain,
            limit=max(1, min(int(limit), 12)),
        )

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_query = _bounded_text(query, field="query", maximum=500)
        clean_domain = None
        if domain is not None:
            clean_domain = _bounded_text(domain, field="domain", maximum=120)
        return self.repo.search_knowledge_items(
            query=clean_query,
            domain=clean_domain,
            limit=max(1, min(int(limit), 50)),
        )
