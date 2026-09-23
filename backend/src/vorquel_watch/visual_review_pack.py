"""Read-only composition of selected frames with existing timed evidence.

A Visual Review Pack is not analysis, knowledge, truth state or instruction.
It is a deterministic projection of evidence already produced by Vorquel
Watch.  The compositor performs no media access, OCR, network call or model
invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Protocol

from vorquel_watch.ids import validate_id
from vorquel_watch.visual_review import SelectedFrame


DATA_TRUST_CLASS = "UNTRUSTED_DERIVED"
INSTRUCTION_AUTHORITY = "NONE"
DEFAULT_TRANSCRIPT_WINDOW_BEFORE_MS = 2_000
DEFAULT_TRANSCRIPT_WINDOW_AFTER_MS = 3_000
MAX_EVIDENCE_ROWS = 200


class VisualReviewEvidenceReader(Protocol):
    """The existing WatchRepository read surface used by the compositor."""

    def observation_at(
        self, source_id: str, timestamp_ms: int
    ) -> dict[str, Any] | None: ...

    def screen_text_blocks(
        self, observation_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def segments_in_range(
        self,
        source_id: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class FrameEvidence:
    artifact_id: str
    sha256: str
    width: int
    height: int

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class ScreenEvidence:
    observation_id: str | None
    ocr_text: str | None
    text_blocks: tuple[dict[str, object], ...]
    start_ms: int | None = None
    end_ms: int | None = None
    representative_frame_ms: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "ocr_text": self.ocr_text,
            "text_blocks": [dict(block) for block in self.text_blocks],
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "representative_frame_ms": self.representative_frame_ms,
        }


@dataclass(frozen=True, slots=True)
class TranscriptEvidence:
    segments: tuple[dict[str, object], ...]
    window_start_ms: int
    window_end_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "segments": [dict(segment) for segment in self.segments],
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
        }


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    source_id: str
    frame_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    text_block_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "frame_ids": list(self.frame_ids),
            "artifact_ids": list(self.artifact_ids),
            "observation_ids": list(self.observation_ids),
            "text_block_ids": list(self.text_block_ids),
            "segment_ids": list(self.segment_ids),
        }


@dataclass(frozen=True, slots=True)
class VisualReviewPack:
    source_id: str
    frame_id: str
    timestamp_ms: int
    selection_reason: str
    frame: FrameEvidence
    screen: ScreenEvidence
    transcript: TranscriptEvidence
    provenance: EvidenceProvenance
    data_trust_class: str = DATA_TRUST_CLASS
    instruction_authority: str = INSTRUCTION_AUTHORITY

    def as_dict(self) -> dict[str, object]:
        """Serialize only safe metadata and untrusted textual content.

        SelectedFrame.image_bytes and every filesystem/workspace detail are
        intentionally absent.  Text itself is preserved verbatim even when it
        resembles a path or command; it remains data under the trust fields.
        """
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "timestamp_ms": self.timestamp_ms,
            "selection_reason": self.selection_reason,
            "frame": self.frame.as_dict(),
            "screen": self.screen.as_dict(),
            "transcript": self.transcript.as_dict(),
            "provenance": self.provenance.as_dict(),
            "data_trust_class": self.data_trust_class,
            "instruction_authority": self.instruction_authority,
        }


def build_visual_review_pack(
    *,
    source_id: str,
    selected_frame: SelectedFrame,
    evidence_reader: VisualReviewEvidenceReader,
    transcript_window_before_ms: int = DEFAULT_TRANSCRIPT_WINDOW_BEFORE_MS,
    transcript_window_after_ms: int = DEFAULT_TRANSCRIPT_WINDOW_AFTER_MS,
) -> VisualReviewPack:
    """Build one pack without changing or generating any evidence."""
    packs = build_visual_review_packs(
        source_id=source_id,
        selected_frames=(selected_frame,),
        evidence_reader=evidence_reader,
        transcript_window_before_ms=transcript_window_before_ms,
        transcript_window_after_ms=transcript_window_after_ms,
    )
    return packs[0]


def build_visual_review_packs(
    *,
    source_id: str,
    selected_frames: Iterable[SelectedFrame],
    evidence_reader: VisualReviewEvidenceReader,
    transcript_window_before_ms: int = DEFAULT_TRANSCRIPT_WINDOW_BEFORE_MS,
    transcript_window_after_ms: int = DEFAULT_TRANSCRIPT_WINDOW_AFTER_MS,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> tuple[VisualReviewPack, ...]:
    """Compose packs in absolute timestamp order.

    Range filtering never rebases timestamps.  Input frames are already
    selected/deduplicated by Visual Review Core; this function does neither.
    """
    source_id = validate_id(source_id, "src_")
    before = _non_negative_int(
        transcript_window_before_ms, "transcript_window_before_ms"
    )
    after = _non_negative_int(
        transcript_window_after_ms, "transcript_window_after_ms"
    )
    range_start, range_end = _validate_range(start_ms, end_ms)

    frames = sorted(selected_frames, key=lambda item: (item.timestamp_ms, item.frame_id))
    packs: list[VisualReviewPack] = []
    for frame in frames:
        if frame.source_id != source_id:
            raise ValueError("selected frame belongs to a different source")
        frame_timestamp = _non_negative_int(frame.timestamp_ms, "frame timestamp_ms")
        if frame_timestamp < range_start:
            continue
        if range_end is not None and frame_timestamp > range_end:
            continue
        packs.append(
            _compose_pack(
                source_id=source_id,
                frame=frame,
                evidence_reader=evidence_reader,
                before_ms=before,
                after_ms=after,
            )
        )
    return tuple(packs)


def _compose_pack(
    *,
    source_id: str,
    frame: SelectedFrame,
    evidence_reader: VisualReviewEvidenceReader,
    before_ms: int,
    after_ms: int,
) -> VisualReviewPack:
    window_start = max(0, frame.timestamp_ms - before_ms)
    window_end = frame.timestamp_ms + after_ms

    observation = evidence_reader.observation_at(source_id, frame.timestamp_ms)
    screen = _screen_evidence(source_id, observation, evidence_reader)

    raw_segments = evidence_reader.segments_in_range(
        source_id,
        start_ms=window_start,
        end_ms=window_end,
        limit=MAX_EVIDENCE_ROWS,
    )
    segments = tuple(
        _segment_projection(source_id, row)
        for row in sorted(
            (
                row
                for row in raw_segments
                if _overlaps(row, window_start, window_end)
            ),
            key=lambda row: (
                int(row["start_ms"]),
                int(row["end_ms"]),
                str(row["segment_id"]),
            ),
        )
    )

    observation_ids = (
        (screen.observation_id,) if screen.observation_id is not None else ()
    )
    text_block_ids = tuple(str(block["ocr_id"]) for block in screen.text_blocks)
    segment_ids = tuple(str(segment["segment_id"]) for segment in segments)
    frame_id = validate_id(frame.frame_id, "art_")
    artifact_id = validate_id(frame.artifact_id, "art_")
    if not re.fullmatch(r"[0-9a-f]{64}", frame.artifact_sha256):
        raise ValueError("frame artifact sha256 is invalid")
    return VisualReviewPack(
        source_id=source_id,
        frame_id=frame_id,
        timestamp_ms=frame.timestamp_ms,
        selection_reason=frame.selection_reason.value,
        frame=FrameEvidence(
            artifact_id=artifact_id,
            sha256=frame.artifact_sha256,
            width=_positive_int(frame.width, "frame width"),
            height=_positive_int(frame.height, "frame height"),
        ),
        screen=screen,
        transcript=TranscriptEvidence(
            segments=segments,
            window_start_ms=window_start,
            window_end_ms=window_end,
        ),
        provenance=EvidenceProvenance(
            source_id=source_id,
            frame_ids=(frame_id,),
            artifact_ids=(artifact_id,),
            observation_ids=observation_ids,
            text_block_ids=text_block_ids,
            segment_ids=segment_ids,
        ),
    )


def _screen_evidence(
    source_id: str,
    observation: dict[str, Any] | None,
    evidence_reader: VisualReviewEvidenceReader,
) -> ScreenEvidence:
    if observation is None:
        return ScreenEvidence(None, None, ())
    _matching_source(observation, source_id, "screen observation")
    observation_id = validate_id(observation.get("observation_id"), "obs_")
    blocks = tuple(
        _text_block_projection(source_id, observation_id, row)
        for row in sorted(
            evidence_reader.screen_text_blocks(
                observation_id, limit=MAX_EVIDENCE_ROWS
            ),
            key=lambda row: (int(row["ordinal"]), str(row["ocr_id"])),
        )
    )
    return ScreenEvidence(
        observation_id=observation_id,
        ocr_text=_optional_text(observation.get("ocr_text")),
        text_blocks=blocks,
        start_ms=_required_non_negative(observation, "start_ms"),
        end_ms=_required_non_negative(observation, "end_ms"),
        representative_frame_ms=_required_non_negative(
            observation, "representative_frame_ms"
        ),
    )


def _text_block_projection(
    source_id: str, observation_id: str, row: dict[str, Any]
) -> dict[str, object]:
    _matching_source(row, source_id, "OCR text block")
    if row.get("observation_id") != observation_id:
        raise ValueError("OCR text block belongs to a different observation")
    return {
        "ocr_id": validate_id(row.get("ocr_id"), "ocr_"),
        "observation_id": observation_id,
        "source_id": source_id,
        "ordinal": _required_non_negative(row, "ordinal"),
        "text": str(row.get("text", "")),
        "confidence": _optional_number(row.get("confidence")),
        "bbox_x": _optional_number(row.get("bbox_x")),
        "bbox_y": _optional_number(row.get("bbox_y")),
        "bbox_width": _optional_number(row.get("bbox_width")),
        "bbox_height": _optional_number(row.get("bbox_height")),
        "data_trust_class": DATA_TRUST_CLASS,
        "instruction_authority": INSTRUCTION_AUTHORITY,
    }


def _segment_projection(source_id: str, row: dict[str, Any]) -> dict[str, object]:
    _matching_source(row, source_id, "transcript segment")
    speaker_id = row.get("speaker_id")
    if speaker_id is not None:
        speaker_id = validate_id(speaker_id, "spk_")
    return {
        "segment_id": validate_id(row.get("segment_id"), "seg_"),
        "transcript_id": validate_id(row.get("transcript_id"), "trn_"),
        "source_id": source_id,
        "ordinal": _required_non_negative(row, "ordinal"),
        "start_ms": _required_non_negative(row, "start_ms"),
        "end_ms": _required_non_negative(row, "end_ms"),
        # effective_text is the repository's reviewed-safe canonical surface;
        # it is copied verbatim and never summarised or interpreted here.
        "effective_text": str(row.get("effective_text", "")),
        "text_origin": _optional_text(row.get("text_origin")),
        "review_status": _optional_text(row.get("review_status")),
        "revision": row.get("revision"),
        "speaker_id": speaker_id,
        "confidence": _optional_number(row.get("confidence")),
        "data_trust_class": DATA_TRUST_CLASS,
        "instruction_authority": INSTRUCTION_AUTHORITY,
    }


def _overlaps(row: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    row_start = _required_non_negative(row, "start_ms")
    row_end = _required_non_negative(row, "end_ms")
    if row_end < row_start:
        raise ValueError("evidence end timestamp precedes start timestamp")
    return row_end >= start_ms and row_start <= end_ms


def _matching_source(row: dict[str, Any], source_id: str, label: str) -> None:
    if row.get("source_id") != source_id:
        raise ValueError(f"{label} belongs to a different source")


def _required_non_negative(row: dict[str, Any], field: str) -> int:
    return _non_negative_int(row.get(field), field)


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _non_negative_int(value, label)
    if result == 0:
        raise ValueError(f"{label} must be positive")
    return result


def _validate_range(
    start_ms: int | None, end_ms: int | None
) -> tuple[int, int | None]:
    start = 0 if start_ms is None else _non_negative_int(start_ms, "start_ms")
    end = None if end_ms is None else _non_negative_int(end_ms, "end_ms")
    if end is not None and end < start:
        raise ValueError("start_ms must not be after end_ms")
    return start, end


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric evidence field has an invalid value")
    return value
