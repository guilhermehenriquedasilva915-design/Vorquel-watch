"""Bounded, provider-neutral semantic drafts over existing Watch evidence.

Drafts are ephemeral review material.  Analysis performs only bounded reads
and never writes a knowledge candidate or item.  A human must pass a selected
draft through ``propose_draft`` to reach the existing KnowledgeWriter boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Protocol, Sequence

from vorquel_watch.ids import validate_id
from vorquel_watch.knowledge import (
    EPISTEMIC_STATUSES,
    KNOWLEDGE_TYPES,
    KnowledgeProvenance,
    KnowledgeWriter,
)
from vorquel_watch.local_storage import LocalStorage
from vorquel_watch.visual_review import SelectionMode, extract_frames
from vorquel_watch.visual_review_pack import (
    DATA_TRUST_CLASS,
    INSTRUCTION_AUTHORITY,
    VisualReviewPack,
    build_visual_review_packs,
)


MAX_ANALYZE_RANGE_MS = 2 * 60 * 1000
MAX_TRANSCRIPT_SEGMENTS = 24
MAX_VISUAL_PACKS = 8
MAX_CONTEXT_CHARACTERS = 12_000
MAX_EVIDENCE_CHARACTERS = 2_000
MAX_DRAFTS_PER_RUN = 5


@dataclass(frozen=True, slots=True)
class AnalyzeBudget:
    max_range_ms: int = MAX_ANALYZE_RANGE_MS
    max_transcript_segments: int = MAX_TRANSCRIPT_SEGMENTS
    max_visual_packs: int = MAX_VISUAL_PACKS
    max_context_characters: int = MAX_CONTEXT_CHARACTERS
    max_evidence_characters: int = MAX_EVIDENCE_CHARACTERS
    max_drafts: int = MAX_DRAFTS_PER_RUN


@dataclass(frozen=True, slots=True)
class DraftEvidenceRef:
    source_id: str
    start_ms: int | None = None
    end_ms: int | None = None
    transcript_id: str | None = None
    segment_id: str | None = None
    screen_observation_id: str | None = None
    frame_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.source_id, "src_")
        for value, prefix in (
            (self.transcript_id, "trn_"),
            (self.segment_id, "seg_"),
            (self.screen_observation_id, "obs_"),
            (self.frame_id, "art_"),
            (self.artifact_id, "art_"),
        ):
            if value is not None:
                validate_id(value, prefix)
        if self.start_ms is not None and (
            isinstance(self.start_ms, bool) or self.start_ms < 0
        ):
            raise ValueError("evidence start_ms must be non-negative")
        if self.end_ms is not None and (
            isinstance(self.end_ms, bool) or self.end_ms < 0
        ):
            raise ValueError("evidence end_ms must be non-negative")
        if (
            self.start_ms is not None
            and self.end_ms is not None
            and self.end_ms < self.start_ms
        ):
            raise ValueError("evidence end_ms precedes start_ms")
        if not any(
            value is not None
            for value in (
                self.start_ms,
                self.end_ms,
                self.transcript_id,
                self.segment_id,
                self.screen_observation_id,
                self.frame_id,
                self.artifact_id,
            )
        ):
            raise ValueError("evidence reference requires a verifiable anchor")

    def as_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "source_id": self.source_id,
                "start_ms": self.start_ms,
                "end_ms": self.end_ms,
                "transcript_id": self.transcript_id,
                "segment_id": self.segment_id,
                "screen_observation_id": self.screen_observation_id,
                "frame_id": self.frame_id,
                "artifact_id": self.artifact_id,
            }.items()
            if value is not None
        }

    def as_knowledge_provenance(self) -> KnowledgeProvenance:
        return KnowledgeProvenance(
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            transcript_id=self.transcript_id,
            segment_id=self.segment_id,
            screen_observation_id=self.screen_observation_id,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DraftEvidenceRef":
        if not isinstance(value, dict):
            raise ValueError("evidence reference must be an object")
        return cls(
            source_id=value.get("source_id"),
            start_ms=value.get("start_ms"),
            end_ms=value.get("end_ms"),
            transcript_id=value.get("transcript_id"),
            segment_id=value.get("segment_id"),
            screen_observation_id=value.get("screen_observation_id"),
            frame_id=value.get("frame_id"),
            artifact_id=value.get("artifact_id"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    text: str
    ref: DraftEvidenceRef


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    source_id: str
    domain: str
    start_ms: int
    end_ms: int
    source_metadata: dict[str, object]
    evidence: tuple[EvidenceItem, ...]
    character_count: int


@dataclass(frozen=True, slots=True)
class DraftProposal:
    title: str
    summary: str
    knowledge_type: str
    epistemic_status: str
    domain: str
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class DraftCandidate:
    draft_id: str
    source_id: str
    title: str
    summary: str
    knowledge_type: str
    epistemic_status: str
    domain: str
    evidence_refs: tuple[DraftEvidenceRef, ...]
    confidence: float | None = None
    rationale: str | None = None
    trust_class: str = DATA_TRUST_CLASS
    instruction_authority: str = INSTRUCTION_AUTHORITY

    def as_dict(self) -> dict[str, object]:
        starts = [ref.start_ms for ref in self.evidence_refs if ref.start_ms is not None]
        ends = [ref.end_ms for ref in self.evidence_refs if ref.end_ms is not None]
        return {
            "draft_id": self.draft_id,
            "source_id": self.source_id,
            "title": self.title,
            "summary": self.summary,
            "knowledge_type": self.knowledge_type,
            "epistemic_status": self.epistemic_status,
            "domain": self.domain,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
            "start_ms": min(starts) if starts else None,
            "end_ms": max(ends) if ends else None,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "trust_class": self.trust_class,
            "instruction_authority": self.instruction_authority,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DraftCandidate":
        if not isinstance(value, dict):
            raise ValueError("draft must be an object")
        refs = tuple(
            DraftEvidenceRef.from_dict(item) for item in value.get("evidence_refs") or []
        )
        draft = _validated_draft(
            source_id=value.get("source_id"),
            proposal=DraftProposal(
                title=value.get("title"),
                summary=value.get("summary"),
                knowledge_type=value.get("knowledge_type"),
                epistemic_status=value.get("epistemic_status"),
                domain=value.get("domain"),
                evidence_ids=tuple(str(index) for index in range(len(refs))),
                confidence=value.get("confidence"),
                rationale=value.get("rationale"),
            ),
            refs=refs,
        )
        supplied_id = value.get("draft_id")
        if supplied_id is not None and supplied_id != draft.draft_id:
            raise ValueError("draft_id does not match draft content")
        return draft


@dataclass(frozen=True, slots=True)
class AnalyzeResult:
    source_id: str
    start_ms: int
    end_ms: int
    transcript_segment_count: int
    visual_pack_count: int
    evidence_count: int
    context_characters: int
    discarded_draft_count: int
    drafts: tuple[DraftCandidate, ...]
    budget: AnalyzeBudget
    data_trust_class: str = DATA_TRUST_CLASS
    instruction_authority: str = INSTRUCTION_AUTHORITY

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "transcript_segment_count": self.transcript_segment_count,
            "visual_pack_count": self.visual_pack_count,
            "evidence_count": self.evidence_count,
            "context_characters": self.context_characters,
            "discarded_draft_count": self.discarded_draft_count,
            "draft_count": len(self.drafts),
            "drafts": [draft.as_dict() for draft in self.drafts],
            "budget": {
                "max_range_ms": self.budget.max_range_ms,
                "max_transcript_segments": self.budget.max_transcript_segments,
                "max_visual_packs": self.budget.max_visual_packs,
                "max_context_characters": self.budget.max_context_characters,
                "max_evidence_characters": self.budget.max_evidence_characters,
                "max_drafts": self.budget.max_drafts,
            },
            "data_trust_class": self.data_trust_class,
            "instruction_authority": self.instruction_authority,
            "auto_persisted": False,
        }


class SemanticAnalyzer(Protocol):
    """Replaceable provider boundary; domain code imports no provider SDK."""

    def analyze_evidence(self, context: EvidenceContext) -> Sequence[DraftProposal]: ...


class HeuristicSemanticAnalyzer:
    """Deterministic local V1 analyzer used until a provider is authorized."""

    def analyze_evidence(self, context: EvidenceContext) -> Sequence[DraftProposal]:
        proposals: list[DraftProposal] = []
        for item in context.evidence:
            text = " ".join(item.text.split())
            if len(text) < 32 or _looks_like_prompt_injection(text):
                continue
            epistemic = "DECLARADO" if item.kind == "TRANSCRIPT" else "OBSERVADO"
            title_text = _first_clause(text)
            title = f"{epistemic}: {title_text[:180].rstrip()}"
            summary = f"{epistemic}: {text}"
            proposals.append(
                DraftProposal(
                    title=title,
                    summary=summary,
                    knowledge_type="CLAIM",
                    epistemic_status=epistemic,
                    domain=context.domain,
                    evidence_ids=(item.evidence_id,),
                    confidence=0.6,
                    rationale=(
                        "Draft local determinístico ancorado em um único trecho "
                        "de evidência; requer revisão humana."
                    ),
                )
            )
        return proposals


PackProvider = Callable[
    [dict[str, Any], int, int, AnalyzeBudget], Sequence[VisualReviewPack]
]


class AnalyzeToCandidateService:
    """Read-only orchestration from a processed source to ephemeral drafts."""

    def __init__(
        self,
        repo: Any,
        settings: Any,
        *,
        analyzer: SemanticAnalyzer | None = None,
        budget: AnalyzeBudget | None = None,
        pack_provider: PackProvider | None = None,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.analyzer = analyzer or HeuristicSemanticAnalyzer()
        self.budget = budget or AnalyzeBudget()
        self.pack_provider = pack_provider or self._visual_packs

    def analyze_source(
        self,
        *,
        source_id: str,
        domain: str,
        start_ms: int = 0,
        end_ms: int | None = None,
    ) -> AnalyzeResult:
        source_id = validate_id(source_id, "src_")
        clean_domain = _bounded_text(domain, "domain", 120)
        source = self.repo.get_source(source_id)
        if not source or source.get("ingest_status") != "READY":
            raise ValueError("source is not ready")
        duration_ms = _non_negative_int(source.get("duration_ms"), "duration_ms")
        start = _non_negative_int(start_ms, "start_ms")
        end = min(duration_ms, start + self.budget.max_range_ms) if end_ms is None else _non_negative_int(end_ms, "end_ms")
        if start >= end or end > duration_ms:
            raise ValueError("analysis range must fit the source timeline")
        if end - start > self.budget.max_range_ms:
            raise ValueError("analysis range exceeds the context budget")

        job_id = source.get("latest_successful_job_id")
        job = self.repo.get_job(job_id) if isinstance(job_id, str) else None
        if not job or job.get("status") != "SUCCEEDED" or job.get("source_id") != source_id:
            raise ValueError("source analysis is not complete")

        segments = self.repo.segments_in_range(
            source_id,
            start_ms=start,
            end_ms=end,
            limit=self.budget.max_transcript_segments,
        )
        packs: Sequence[VisualReviewPack] = ()
        if source.get("has_video"):
            packs = self.pack_provider(source, start, end, self.budget)
            packs = tuple(packs[: self.budget.max_visual_packs])

        context = select_evidence(
            source=source,
            domain=clean_domain,
            start_ms=start,
            end_ms=end,
            transcript_segments=segments,
            visual_packs=packs,
            budget=self.budget,
        )
        proposals = self.analyzer.analyze_evidence(context) if context.evidence else ()
        drafts, discarded = validate_and_deduplicate_drafts(
            source_id=source_id,
            proposals=proposals,
            context=context,
            max_drafts=self.budget.max_drafts,
        )
        return AnalyzeResult(
            source_id=source_id,
            start_ms=start,
            end_ms=end,
            transcript_segment_count=len(segments),
            visual_pack_count=len(packs),
            evidence_count=len(context.evidence),
            context_characters=context.character_count,
            discarded_draft_count=discarded,
            drafts=drafts,
            budget=self.budget,
        )

    def _visual_packs(
        self,
        source: dict[str, Any],
        start_ms: int,
        end_ms: int,
        budget: AnalyzeBudget,
    ) -> Sequence[VisualReviewPack]:
        media_path = LocalStorage(self.settings.data_dir).verify_object(
            str(source["content_sha256"])
        )
        selection = extract_frames(
            media_path,
            source_id=str(source["source_id"]),
            mode=SelectionMode.EFFICIENT,
            start_ms=start_ms,
            end_ms=end_ms,
            max_frames=budget.max_visual_packs,
        )
        return build_visual_review_packs(
            source_id=str(source["source_id"]),
            selected_frames=selection.frames,
            evidence_reader=self.repo,
            start_ms=start_ms,
            end_ms=end_ms,
        )


def select_evidence(
    *,
    source: dict[str, Any],
    domain: str,
    start_ms: int,
    end_ms: int,
    transcript_segments: Sequence[dict[str, Any]],
    visual_packs: Sequence[VisualReviewPack],
    budget: AnalyzeBudget,
) -> EvidenceContext:
    source_id = validate_id(source.get("source_id"), "src_")
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    used = 0

    def add(item: EvidenceItem) -> None:
        nonlocal used
        if item.evidence_id in seen:
            return
        text = " ".join(item.text.split())[: budget.max_evidence_characters]
        if not text or used + len(text) > budget.max_context_characters:
            return
        seen.add(item.evidence_id)
        items.append(EvidenceItem(item.evidence_id, item.kind, text, item.ref))
        used += len(text)

    for row in sorted(
        transcript_segments[: budget.max_transcript_segments],
        key=lambda value: (int(value["start_ms"]), str(value["segment_id"])),
    ):
        if row.get("source_id") != source_id:
            raise ValueError("transcript evidence belongs to another source")
        segment_id = validate_id(row.get("segment_id"), "seg_")
        add(
            EvidenceItem(
                evidence_id=f"segment:{segment_id}",
                kind="TRANSCRIPT",
                text=str(row.get("effective_text") or ""),
                ref=DraftEvidenceRef(
                    source_id=source_id,
                    start_ms=_non_negative_int(row.get("start_ms"), "start_ms"),
                    end_ms=_non_negative_int(row.get("end_ms"), "end_ms"),
                    transcript_id=validate_id(row.get("transcript_id"), "trn_"),
                    segment_id=segment_id,
                ),
            )
        )

    for pack in sorted(
        visual_packs[: budget.max_visual_packs], key=lambda value: value.timestamp_ms
    ):
        if pack.source_id != source_id:
            raise ValueError("visual evidence belongs to another source")
        observation_id = pack.screen.observation_id
        text = pack.screen.ocr_text or " ".join(
            str(block.get("text") or "") for block in pack.screen.text_blocks
        )
        if not text:
            continue
        evidence_id = (
            f"observation:{observation_id}"
            if observation_id is not None
            else f"frame:{pack.frame_id}"
        )
        add(
            EvidenceItem(
                evidence_id=evidence_id,
                kind="SCREEN",
                text=text,
                ref=DraftEvidenceRef(
                    source_id=source_id,
                    start_ms=pack.screen.start_ms if observation_id else pack.timestamp_ms,
                    end_ms=pack.screen.end_ms if observation_id else pack.timestamp_ms,
                    screen_observation_id=observation_id,
                    frame_id=pack.frame_id,
                    artifact_id=pack.frame.artifact_id,
                ),
            )
        )

    metadata = {
        key: source.get(key)
        for key in (
            "source_id",
            "source_kind",
            "detected_mime",
            "duration_ms",
            "has_video",
            "has_audio",
            "latest_transcript_id",
        )
    }
    return EvidenceContext(
        source_id=source_id,
        domain=domain,
        start_ms=start_ms,
        end_ms=end_ms,
        source_metadata=metadata,
        evidence=tuple(items),
        character_count=used,
    )


def validate_and_deduplicate_drafts(
    *,
    source_id: str,
    proposals: Sequence[DraftProposal],
    context: EvidenceContext,
    max_drafts: int,
) -> tuple[tuple[DraftCandidate, ...], int]:
    evidence = {item.evidence_id: item.ref for item in context.evidence}
    drafts: list[DraftCandidate] = []
    discarded = 0
    for proposal in proposals:
        if not isinstance(proposal, DraftProposal):
            discarded += 1
            continue
        try:
            refs = tuple(evidence[item] for item in proposal.evidence_ids)
            if not refs or len(refs) != len(proposal.evidence_ids):
                raise ValueError("draft evidence is missing")
            draft = _validated_draft(
                source_id=source_id,
                proposal=proposal,
                refs=refs,
            )
        except (KeyError, TypeError, ValueError):
            discarded += 1
            continue
        if any(_equivalent_draft(draft, existing) for existing in drafts):
            discarded += 1
            continue
        drafts.append(draft)
        if len(drafts) >= max_drafts:
            discarded += max(0, len(proposals) - len(drafts) - discarded)
            break
    return tuple(drafts), discarded


def propose_draft(writer: KnowledgeWriter, draft: DraftCandidate) -> dict[str, Any]:
    """Explicit human boundary; never called by ``analyze_source``."""
    return writer.propose(
        source_id=draft.source_id,
        knowledge_type=draft.knowledge_type,
        domain=draft.domain,
        title=draft.title,
        summary=draft.summary,
        epistemic_status=draft.epistemic_status,
        provenance=[ref.as_knowledge_provenance() for ref in draft.evidence_refs],
    )


def _validated_draft(
    *,
    source_id: object,
    proposal: DraftProposal,
    refs: tuple[DraftEvidenceRef, ...],
) -> DraftCandidate:
    canonical_source = validate_id(source_id, "src_")
    if not refs or any(ref.source_id != canonical_source for ref in refs):
        raise ValueError("draft requires same-source evidence")
    title = _bounded_text(proposal.title, "title", 300)
    summary = _bounded_text(proposal.summary, "summary", 12_000)
    domain = _bounded_text(proposal.domain, "domain", 120)
    knowledge_type = str(proposal.knowledge_type).strip().upper()
    epistemic = str(proposal.epistemic_status).strip().upper()
    if knowledge_type not in KNOWLEDGE_TYPES:
        raise ValueError("unsupported knowledge_type")
    if epistemic not in EPISTEMIC_STATUSES:
        raise ValueError("unsupported epistemic_status")
    confidence = proposal.confidence
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
    rationale = None
    if proposal.rationale is not None:
        rationale = _bounded_text(proposal.rationale, "rationale", 2_000)
    material = "\x1f".join(
        [canonical_source, knowledge_type, domain, title, summary, epistemic]
        + [str(ref.as_dict()) for ref in refs]
    ).encode("utf-8")
    return DraftCandidate(
        draft_id="draft_" + hashlib.sha256(material).hexdigest()[:24],
        source_id=canonical_source,
        title=title,
        summary=summary,
        knowledge_type=knowledge_type,
        epistemic_status=epistemic,
        domain=domain,
        evidence_refs=refs,
        confidence=confidence,
        rationale=rationale,
    )


def _equivalent_draft(left: DraftCandidate, right: DraftCandidate) -> bool:
    if (left.knowledge_type, left.domain.casefold()) != (
        right.knowledge_type,
        right.domain.casefold(),
    ):
        return False
    same_text = (
        _normalized(left.title) == _normalized(right.title)
        or _normalized(left.summary) == _normalized(right.summary)
    )
    left_refs = {str(ref.as_dict()) for ref in left.evidence_refs}
    right_refs = {str(ref.as_dict()) for ref in right.evidence_refs}
    return same_text or bool(left_refs & right_refs)


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _first_clause(value: str) -> str:
    pieces = re.split(r"(?<=[.!?])\s+", value, maxsplit=1)
    return pieces[0]


def _looks_like_prompt_injection(value: str) -> bool:
    normalized = value.casefold()
    markers = (
        "ignore previous instruction",
        "ignore all previous",
        "call tool",
        "execute command",
        "system prompt",
        "read ~/.ssh",
        "leia c:\\users",
    )
    return any(marker in normalized for marker in markers)


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    clean = value.strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{field} is required and must be <= {maximum} chars")
    return clean


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value
