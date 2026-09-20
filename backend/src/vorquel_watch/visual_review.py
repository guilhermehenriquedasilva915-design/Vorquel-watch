"""Deterministic frame selection for Visual Review Core V1.

The core selects evidence; it does not interpret it.  Media pixels and any
text visible in them remain untrusted data.  Candidate thumbnails live only in
memory, and full-resolution images are rendered only after dedupe and budget
enforcement.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from vorquel_watch.frames import change_score, read_timeline
from vorquel_watch.ids import stable_id, validate_id


class SelectionMode(str, Enum):
    EFFICIENT = "efficient"
    BALANCED = "balanced"
    DETAILED = "detailed"


class SelectionReason(str, Enum):
    SCENE_CHANGE = "SCENE_CHANGE"
    KEYFRAME = "KEYFRAME"
    UNIFORM = "UNIFORM"
    TRANSCRIPT_CUE = "TRANSCRIPT_CUE"
    FOCUSED = "FOCUSED"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    max_frames: int
    focused_max_frames: int
    target_interval_ms: int
    focused_interval_ms: int
    minimum_primary_candidates: int
    scene_threshold: float


# One visible, testable policy table; there are no hidden per-mode caps.
BUDGET_PROFILES: dict[SelectionMode, BudgetProfile] = {
    SelectionMode.EFFICIENT: BudgetProfile(40, 80, 8_000, 1_000, 4, 0.08),
    SelectionMode.BALANCED: BudgetProfile(100, 160, 4_000, 500, 4, 0.06),
    SelectionMode.DETAILED: BudgetProfile(200, 300, 2_000, 250, 6, 0.04),
}

DEFAULT_MODE = SelectionMode.BALANCED
DEFAULT_RESOLUTION = 512
MIN_RESOLUTION = 128
MAX_RESOLUTION = 2048
DEDUP_FINGERPRINT_SIZE = 64
# Normalised mean absolute brightness difference.  Deliberately conservative:
# roughly 1.5 grayscale levels, below the existing screen-grouping threshold.
DEFAULT_DEDUP_THRESHOLD = 1.5 / 255.0


@dataclass(frozen=True, slots=True)
class FrameBudget:
    mode: SelectionMode
    focused: bool
    target_count: int
    hard_cap: int
    interval_ms: int


@dataclass(frozen=True, slots=True)
class SelectedFrame:
    frame_id: str
    source_id: str
    timestamp_ms: int
    selection_reason: SelectionReason
    width: int
    height: int
    artifact_id: str
    artifact_sha256: str
    data_trust_class: str = "UNTRUSTED_DERIVED"
    instruction_authority: str = "NONE"
    image_bytes: bytes = b""

    def as_dict(self) -> dict[str, object]:
        """Return safe external metadata; local paths and pixels are excluded."""
        return {
            "frame_id": self.frame_id,
            "source_id": self.source_id,
            "timestamp_ms": self.timestamp_ms,
            "selection_reason": self.selection_reason.value,
            "width": self.width,
            "height": self.height,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "data_trust_class": self.data_trust_class,
            "instruction_authority": self.instruction_authority,
        }


@dataclass(frozen=True, slots=True)
class VisualReviewResult:
    source_id: str
    mode: SelectionMode
    start_ms: int
    end_ms: int
    candidate_count: int
    deduplicated_count: int
    kept_count: int
    fallback_used: bool
    frames: tuple[SelectedFrame, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    timestamp_ms: int
    reason: SelectionReason
    thumbnail: np.ndarray
    pinned: bool = False


def frame_budget(
    duration_ms: int,
    *,
    mode: SelectionMode | str = DEFAULT_MODE,
    focused: bool = False,
    max_frames: int | None = None,
) -> FrameBudget:
    """Compute a duration-aware budget bounded by a central hard cap."""
    selected_mode = SelectionMode(mode)
    if duration_ms < 0:
        raise ValueError("duration must not be negative")
    profile = BUDGET_PROFILES[selected_mode]
    hard_cap = profile.focused_max_frames if focused else profile.max_frames
    if max_frames is not None:
        if max_frames < 2:
            raise ValueError("max_frames must allow first and last frame coverage")
        hard_cap = min(hard_cap, max_frames)
    interval = profile.focused_interval_ms if focused else profile.target_interval_ms
    target = max(2, math.ceil(duration_ms / interval) + 1)
    return FrameBudget(selected_mode, focused, min(target, hard_cap), hard_cap, interval)


def extract_frames(
    media_path: Path,
    *,
    source_id: str,
    mode: SelectionMode | str = DEFAULT_MODE,
    timestamps_ms: Iterable[int] = (),
    start_ms: int | None = None,
    end_ms: int | None = None,
    resolution: int = DEFAULT_RESOLUTION,
    max_frames: int | None = None,
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD,
    output_dir: Path | None = None,
) -> VisualReviewResult:
    """Select and render bounded visual evidence from a local video.

    Explicit timestamps are pinned through dedupe and cap.  If their count is
    larger than the configured hard cap, the request fails explicitly rather
    than silently discarding caller-requested evidence.
    """
    source_id = validate_id(source_id, "src_")
    media_path = Path(media_path)
    selected_mode = SelectionMode(mode)
    if not MIN_RESOLUTION <= resolution <= MAX_RESOLUTION:
        raise ValueError(
            f"resolution must be between {MIN_RESOLUTION} and {MAX_RESOLUTION}"
        )
    if not 0.0 <= dedup_threshold <= 1.0:
        raise ValueError("dedup_threshold must be between 0 and 1")

    timeline = read_timeline(media_path)
    window_start, window_end, focused = _validate_window(
        timeline.duration_ms, start_ms, end_ms
    )
    cues = _validate_timestamps(timestamps_ms, timeline.duration_ms)
    cues = [stamp for stamp in cues if window_start <= stamp <= window_end]
    budget = frame_budget(
        window_end - window_start,
        mode=selected_mode,
        focused=focused,
        max_frames=max_frames,
    )
    if len(cues) > budget.hard_cap:
        raise ValueError("pinned timestamps exceed the configured frame cap")

    profile = BUDGET_PROFILES[selected_mode]
    fallback_used = False
    try:
        if selected_mode is SelectionMode.EFFICIENT:
            primary = _keyframe_candidates(media_path, window_start, window_end)
        else:
            primary = _scene_candidates(
                media_path,
                window_start,
                window_end,
                profile.scene_threshold,
            )
    except Exception:
        # Candidate discovery is an enhancement.  A valid video must still get
        # deterministic temporal coverage if the chosen detector fails.
        primary = []
        fallback_used = True

    candidates = list(primary)
    if len(candidates) < profile.minimum_primary_candidates:
        fallback_used = True
        reason = SelectionReason.FOCUSED if focused else SelectionReason.FALLBACK
        candidates.extend(
            _uniform_candidates(
                media_path,
                window_start,
                window_end,
                budget.interval_ms,
                reason,
            )
        )

    # Pinned requests are resolved with the same PTS-aware decoder.  Merging is
    # by actual timestamp so a cue overrides a detector reason for that frame.
    for stamp in cues:
        candidate = _candidate_at(
            media_path, stamp, window_start, window_end, SelectionReason.TRANSCRIPT_CUE
        )
        candidates.append(
            _Candidate(
                candidate.timestamp_ms,
                candidate.reason,
                candidate.thumbnail,
                pinned=True,
            )
        )

    candidates = _merge_candidates(candidates)
    candidate_count = len(candidates)
    deduplicated = _deduplicate(candidates, dedup_threshold)
    deduplicated_count = len(deduplicated)
    effective_target = min(
        budget.hard_cap,
        max(budget.target_count, sum(candidate.pinned for candidate in deduplicated)),
    )
    selected = _evenly_cap(deduplicated, effective_target)

    rendered = tuple(
        _render_selected(media_path, source_id, candidate, resolution, output_dir)
        for candidate in selected
    )
    return VisualReviewResult(
        source_id=source_id,
        mode=selected_mode,
        start_ms=window_start,
        end_ms=window_end,
        candidate_count=candidate_count,
        deduplicated_count=deduplicated_count,
        kept_count=len(rendered),
        fallback_used=fallback_used,
        frames=rendered,
    )


def _import_av():
    try:
        import av
    except ImportError as exc:  # pragma: no cover - install shape
        raise RuntimeError(
            "Media support is not installed. Install vorquel-watch[transcribe]."
        ) from exc
    return av


def _validate_window(
    duration_ms: int, start_ms: int | None, end_ms: int | None
) -> tuple[int, int, bool]:
    start = 0 if start_ms is None else start_ms
    end = duration_ms if end_ms is None else end_ms
    if start < 0 or end < 0:
        raise ValueError("focused range must not be negative")
    if start >= end:
        raise ValueError("focused range start must be before end")
    if end > duration_ms:
        raise ValueError("focused range exceeds video duration")
    return start, end, start_ms is not None or end_ms is not None


def _validate_timestamps(values: Iterable[int], duration_ms: int) -> list[int]:
    result: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("timestamps must be integer milliseconds")
        if value < 0 or value >= duration_ms:
            raise ValueError("timestamp is outside the video timeline")
        result.append(value)
    return sorted(set(result))


def _decoded_frames(
    path: Path, start_ms: int, end_ms: int
) -> Iterator[tuple[object, int]]:
    av = _import_av()
    with av.open(str(path), mode="r") as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("media contains no video stream")
        stream.thread_type = "AUTO"
        if start_ms:
            offset = int((start_ms / 1000.0) / float(stream.time_base))
            try:
                container.seek(offset, stream=stream, backward=True, any_frame=False)
            except Exception:
                container.seek(0)
        for frame in container.decode(video=0):
            if frame.time is None:
                continue
            timestamp_ms = int(frame.time * 1000)
            if timestamp_ms < start_ms:
                continue
            if timestamp_ms > end_ms:
                break
            yield frame, timestamp_ms


def _thumbnail(frame) -> np.ndarray:
    small = frame.reformat(
        width=DEDUP_FINGERPRINT_SIZE,
        height=DEDUP_FINGERPRINT_SIZE,
        format="gray",
    )
    return small.to_ndarray().astype(np.int16)


def _scene_candidates(
    path: Path, start_ms: int, end_ms: int, threshold: float
) -> list[_Candidate]:
    result: list[_Candidate] = []
    last_kept: np.ndarray | None = None
    last_seen: _Candidate | None = None
    for frame, timestamp_ms in _decoded_frames(path, start_ms, end_ms):
        thumb = _thumbnail(frame)
        candidate = _Candidate(timestamp_ms, SelectionReason.SCENE_CHANGE, thumb)
        last_seen = candidate
        if last_kept is None or change_score(last_kept, thumb) >= threshold:
            result.append(candidate)
            last_kept = thumb
    if last_seen is not None and (
        not result or last_seen.timestamp_ms != result[-1].timestamp_ms
    ):
        result.append(
            _Candidate(last_seen.timestamp_ms, SelectionReason.UNIFORM, last_seen.thumbnail)
        )
    return result


def _keyframe_candidates(path: Path, start_ms: int, end_ms: int) -> list[_Candidate]:
    """Decode keyframes only, plus short seeks for exact range boundaries."""
    av = _import_av()
    result: list[_Candidate] = []
    with av.open(str(path), mode="r") as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("media contains no video stream")
        # Official PyAV/FFmpeg fast path: discard every non-key frame in the
        # decoder rather than decoding all frames and filtering in Python.
        stream.codec_context.skip_frame = "NONKEY"
        if start_ms:
            offset = int((start_ms / 1000.0) / float(stream.time_base))
            try:
                container.seek(offset, stream=stream, backward=True, any_frame=False)
            except Exception:
                container.seek(0)
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            timestamp_ms = int(frame.time * 1000)
            if timestamp_ms < start_ms:
                continue
            if timestamp_ms > end_ms:
                break
            result.append(
                _Candidate(timestamp_ms, SelectionReason.KEYFRAME, _thumbnail(frame))
            )

    first = _candidate_at(
        path, start_ms, start_ms, end_ms, SelectionReason.UNIFORM
    )
    last = _last_candidate(
        path, max(start_ms, end_ms - 2_000), end_ms, SelectionReason.UNIFORM
    )
    if not any(item.timestamp_ms == first.timestamp_ms for item in result):
        result.insert(0, first)
    if not any(item.timestamp_ms == last.timestamp_ms for item in result):
        result.append(last)
    return result


def _uniform_candidates(
    path: Path,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    reason: SelectionReason,
) -> list[_Candidate]:
    targets = list(range(start_ms, end_ms + 1, max(1, interval_ms)))
    if not targets or targets[-1] != end_ms:
        targets.append(end_ms)
    result: list[_Candidate] = []
    target_index = 0
    last: _Candidate | None = None
    for frame, timestamp_ms in _decoded_frames(path, start_ms, end_ms):
        current = _Candidate(timestamp_ms, reason, _thumbnail(frame))
        last = current
        while target_index < len(targets) and timestamp_ms >= targets[target_index]:
            result.append(current)
            target_index += 1
    if last is not None and (
        not result or result[-1].timestamp_ms != last.timestamp_ms
    ):
        result.append(last)
    return result


def _candidate_at(
    path: Path,
    timestamp_ms: int,
    window_start: int,
    window_end: int,
    reason: SelectionReason,
    *,
    pinned: bool = False,
) -> _Candidate:
    # Decode from the cue so the returned timestamp remains absolute.  A codec
    # may land just after the requested instant; the actual PTS is recorded.
    for frame, actual_ms in _decoded_frames(path, timestamp_ms, window_end):
        if actual_ms >= window_start:
            return _Candidate(actual_ms, reason, _thumbnail(frame), pinned=pinned)
    raise ValueError("no frame available at requested timestamp")


def _last_candidate(
    path: Path,
    decode_start_ms: int,
    window_end: int,
    reason: SelectionReason,
) -> _Candidate:
    last: _Candidate | None = None
    for frame, actual_ms in _decoded_frames(path, decode_start_ms, window_end):
        last = _Candidate(actual_ms, reason, _thumbnail(frame))
    if last is None:
        raise ValueError("no frame available at end of requested range")
    return last


def _merge_candidates(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    by_timestamp: dict[int, _Candidate] = {}
    for candidate in candidates:
        previous = by_timestamp.get(candidate.timestamp_ms)
        # A fallback boundary is useful provenance when it filled a sparse
        # primary result.  Let it replace a generic UNIFORM boundary, while a
        # real scene/keyframe reason remains the more specific explanation.
        fallback_over_boundary = (
            previous is not None
            and previous.reason is SelectionReason.UNIFORM
            and candidate.reason in {SelectionReason.FALLBACK, SelectionReason.FOCUSED}
        )
        if previous is None or candidate.pinned or fallback_over_boundary:
            by_timestamp[candidate.timestamp_ms] = candidate
    return [by_timestamp[stamp] for stamp in sorted(by_timestamp)]


def _deduplicate(
    candidates: list[_Candidate], threshold: float
) -> list[_Candidate]:
    kept: list[_Candidate] = []
    last_thumbnail: np.ndarray | None = None
    for index, candidate in enumerate(candidates):
        boundary = index == 0 or index == len(candidates) - 1
        if candidate.pinned or boundary or last_thumbnail is None:
            kept.append(candidate)
            last_thumbnail = candidate.thumbnail
            continue
        try:
            duplicate = change_score(last_thumbnail, candidate.thumbnail) <= threshold
        except Exception:
            duplicate = False  # fail open: evidence is kept
        if not duplicate:
            kept.append(candidate)
            last_thumbnail = candidate.thumbnail
    return kept


def _evenly_cap(candidates: list[_Candidate], cap: int) -> list[_Candidate]:
    if len(candidates) <= cap:
        return candidates
    mandatory = {0, len(candidates) - 1}
    mandatory.update(index for index, item in enumerate(candidates) if item.pinned)
    if len(mandatory) > cap:
        raise ValueError("pinned frames and temporal boundaries exceed frame cap")
    available = [index for index in range(len(candidates)) if index not in mandatory]
    slots = cap - len(mandatory)
    selected = set(mandatory)
    if slots:
        for ordinal in range(slots):
            position = round(ordinal * (len(available) - 1) / max(1, slots - 1))
            selected.add(available[position])
    return [candidates[index] for index in sorted(selected)]


def _render_selected(
    path: Path,
    source_id: str,
    candidate: _Candidate,
    resolution: int,
    output_dir: Path | None,
) -> SelectedFrame:
    av = _import_av()
    chosen = None
    actual_ms = candidate.timestamp_ms
    for frame, timestamp_ms in _decoded_frames(path, candidate.timestamp_ms, candidate.timestamp_ms + 2_000):
        chosen = frame
        actual_ms = timestamp_ms
        break
    if chosen is None:
        raise ValueError("selected frame could not be rendered")

    width = resolution
    height = max(1, round(chosen.height * (width / chosen.width)))
    rendered = chosen.reformat(width=width, height=height, format="rgb24")
    codec = av.CodecContext.create("png", "w")
    codec.width, codec.height, codec.pix_fmt = width, height, "rgb24"
    packets = codec.encode(rendered) + codec.encode(None)
    image = b"".join(bytes(packet) for packet in packets)
    digest = hashlib.sha256(image).hexdigest()
    frame_id = stable_id("art_", "visual-frame", source_id, actual_ms)
    artifact_id = stable_id("art_", "visual-payload", frame_id, digest)

    if output_dir is not None:
        workspace = Path(output_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        target = (workspace / f"{artifact_id}.png").resolve()
        if workspace not in target.parents:
            raise ValueError("frame artifact escaped output directory")
        target.write_bytes(image)

    return SelectedFrame(
        frame_id=frame_id,
        source_id=source_id,
        timestamp_ms=actual_ms,
        selection_reason=candidate.reason,
        width=width,
        height=height,
        artifact_id=artifact_id,
        artifact_sha256=digest,
        image_bytes=image,
    )
