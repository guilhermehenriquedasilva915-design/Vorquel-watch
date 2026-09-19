"""Turn a video into screen observations.

Walks the frame timeline, groups stretches where the screen did not change,
reads text from one representative frame per stretch, and produces the rows the
database expects.

The expensive step is OCR, so it runs once per distinct screen rather than once
per sample. A slide shown, navigated away from, and returned to reuses the
reading already taken.

Recognising that repeat is done by comparing thumbnails, not by comparing
digests. A lossy codec re-encodes identical content differently after a scene
change: measured drift is about 1.8 units across 95% of pixels, which changes
the digest completely while leaving the picture visually identical. Measured
separation is wide - 0.007 between two views of one screen against 0.134
between different screens - so a similarity threshold distinguishes them
comfortably.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from vorquel_watch.frames import (
    DEFAULT_CHANGE_THRESHOLD,
    DEFAULT_INTERVAL_MS,
    ObservationSpan,
    change_score,
    frame_at,
    group_observations,
    read_timeline,
    sample_frames,
)
from vorquel_watch.ids import new_id
from vorquel_watch.ocr import OcrResult, ScreenOcr


# A bound on how much screen history one job may produce. A recording whose
# screen changes constantly would otherwise generate rows without limit.
DEFAULT_MAX_OBSERVATIONS = 4000

# Two thumbnails this close are the same screen. Measured on re-encoded
# content: two views of one screen differ by 0.007, two different screens by
# 0.134, so this sits with roughly four times margin on either side.
SCREEN_SIMILARITY_THRESHOLD = 0.03

# Bound the linear scan. A recording with thousands of distinct screens would
# otherwise make reuse cost grow with the number of screens already seen.
MAX_REMEMBERED_SCREENS = 512


@dataclass(frozen=True, slots=True)
class ScreenResult:
    observations: list[dict]
    text_blocks: list[dict]
    frames_sampled: int
    observations_kept: int
    ocr_runs: int
    ocr_reused: int
    duration_ms: int


class ScreenCancelled(Exception):
    """The job stopped being ours while the screen pass was running."""


def build_screen_observations(
    media_path: Path,
    *,
    source_id: str,
    job_id: str,
    ocr: ScreenOcr | None = None,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    max_samples: int = 20000,
    keepalive: Callable[[], bool] | None = None,
) -> ScreenResult:
    """Produce screen_observations and screen_text_blocks rows for a video.

    keepalive is called periodically and must return False when the job is no
    longer ours, at which point this raises rather than continuing to spend
    minutes of OCR on work that will be discarded.
    """
    timeline = read_timeline(media_path)
    reader = ocr or ScreenOcr()

    samples = []
    for index, sample in enumerate(
        sample_frames(
            media_path,
            interval_ms=interval_ms,
            change_threshold=change_threshold,
            max_samples=max_samples + 1,
        )
    ):
        samples.append(sample)
        if len(samples) > max_samples:
            raise ValueError("screen sampling exceeds configured limit")
        if keepalive is not None and index % 200 == 0 and not keepalive():
            raise ScreenCancelled()

    spans = group_observations(
        samples,
        duration_ms=timeline.duration_ms,
        change_threshold=change_threshold,
    )[:max_observations]

    observations: list[dict] = []
    text_blocks: list[dict] = []
    # Screens already read: (thumbnail, content_hash, reading). A repeat adopts
    # the first occurrence's content_hash, so equal screens share one hash in
    # the database and the index on it means what it claims to.
    seen: list[tuple] = []
    ocr_runs = 0

    with tempfile.TemporaryDirectory(prefix="vw-screen-") as workspace:
        scratch = Path(workspace)
        for position, span in enumerate(spans):
            if keepalive is not None and position % 10 == 0 and not keepalive():
                raise ScreenCancelled()

            match = _match_seen(span, seen)
            if match is None:
                reading = _read_span(reader, media_path, span, scratch)
                content_hash = span.content_hash
                ocr_runs += 1
                if len(seen) < MAX_REMEMBERED_SCREENS:
                    seen.append((span.thumbnail, content_hash, reading))
            else:
                content_hash, reading = match

            observation_id = new_id("obs_")
            observations.append(
                {
                    "observation_id": observation_id,
                    "schema_version": "1.0",
                    "source_id": source_id,
                    "job_id": job_id,
                    "start_ms": span.start_ms,
                    "end_ms": span.end_ms,
                    "representative_frame_ms": span.representative_frame_ms,
                    "visual_change_score": min(1.0, max(0.0, span.change_score)),
                    "content_hash": content_hash,
                    "ocr_text": reading.text,
                    "ocr_engine": reading.engine,
                    "ocr_engine_version": reading.engine_version,
                    "frames_sampled": span.frames_sampled,
                    "data_trust_class": "UNTRUSTED_DERIVED",
                    "instruction_authority": "NONE",
                }
            )

            for ordinal, block in enumerate(reading.blocks):
                text_blocks.append(
                    {
                        "ocr_id": new_id("ocr_"),
                        "schema_version": "1.0",
                        "observation_id": observation_id,
                        "source_id": source_id,
                        "ordinal": ordinal,
                        "text": block.text,
                        "confidence": block.confidence,
                        "bbox_x": block.bbox_x,
                        "bbox_y": block.bbox_y,
                        "bbox_width": block.bbox_width,
                        "bbox_height": block.bbox_height,
                        "data_trust_class": "UNTRUSTED_DERIVED",
                        "instruction_authority": "NONE",
                    }
                )

    return ScreenResult(
        observations=observations,
        text_blocks=text_blocks,
        frames_sampled=len(samples),
        observations_kept=len(observations),
        ocr_runs=ocr_runs,
        ocr_reused=len(observations) - ocr_runs,
        duration_ms=timeline.duration_ms,
    )


def _match_seen(span: ObservationSpan, seen: list) -> tuple | None:
    """Find a screen already read that looks the same as this one."""
    if span.thumbnail is None:
        # No thumbnail to compare: fall back to exact digest equality, which is
        # correct but only catches byte-identical frames.
        for _thumb, content_hash, reading in seen:
            if content_hash == span.content_hash:
                return content_hash, reading
        return None

    for thumbnail, content_hash, reading in seen:
        if thumbnail is None:
            continue
        if change_score(thumbnail, span.thumbnail) <= SCREEN_SIMILARITY_THRESHOLD:
            return content_hash, reading
    return None


def _read_span(
    reader: ScreenOcr,
    media_path: Path,
    span: ObservationSpan,
    scratch: Path,
) -> OcrResult:
    """OCR one representative frame, through a file in a controlled workspace."""
    png, _actual_ms = frame_at(media_path, span.representative_frame_ms)
    target = scratch / f"{span.content_hash[:16]}.png"
    target.write_bytes(png)
    try:
        return reader.read(str(target))
    finally:
        target.unlink(missing_ok=True)
