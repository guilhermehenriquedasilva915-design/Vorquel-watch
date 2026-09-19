"""Frame timeline, sampling and visual change detection.

This module answers "what was on screen, and when" without persisting frames.
A five-hour recording at 30fps is over half a million frames; indexing them is
neither affordable nor necessary, because the media file is itself the index.
Frames are addressed by timestamp and materialised on demand.

Timestamps come from presentation timestamps, never from frame_number divided
by an assumed frame rate. That assumption is wrong for variable frame rate
recordings, which screen captures frequently are.

Deliberately dependency-free beyond PyAV and numpy, both of which the
transcription runtime already installs: scaling is done by libswscale through
PyAV's reformat, so no OpenCV or Pillow is needed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


# The fingerprint is a tiny grayscale thumbnail. Small enough that comparing
# two of them is free, large enough that a dialog opening or a slide changing
# moves it well clear of encoder noise.
FINGERPRINT_SIZE = 32

# Defaults chosen to be conservative and then measured, not asserted.
DEFAULT_INTERVAL_MS = 1500
DEFAULT_CHANGE_THRESHOLD = 0.08
# After a change, sample more densely for a moment: transitions are where the
# interesting content appears, and a fixed interval can straddle them.
BURST_INTERVAL_MS = 400
BURST_DURATION_MS = 2000


@dataclass(frozen=True, slots=True)
class VideoTimeline:
    duration_ms: int
    average_fps: float
    guessed_fps: float
    variable_frame_rate: bool
    header_frame_count: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FrameSample:
    timestamp_ms: int
    change_score: float
    digest: str
    keyframe: bool


@dataclass(frozen=True, slots=True)
class ObservationSpan:
    """A stretch of time during which the screen did not meaningfully change."""

    start_ms: int
    end_ms: int
    representative_frame_ms: int
    change_score: float
    content_hash: str
    frames_sampled: int


def _import_av():
    try:
        import av
    except ImportError as exc:  # pragma: no cover - exercised by install shape
        raise RuntimeError(
            "Media support is not installed. Install vorquel-watch[transcribe]."
        ) from exc
    return av


def _rational(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_timeline(path: Path) -> VideoTimeline:
    """Describe the video's timing, including whether it is variable rate."""
    av = _import_av()
    with av.open(str(path), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError("media contains no video stream")

        average = _rational(stream.average_rate)
        guessed = _rational(getattr(stream, "guessed_rate", None)) or average
        duration_ms = 0
        if container.duration is not None:
            duration_ms = max(0, int((container.duration / av.time_base) * 1000))

        return VideoTimeline(
            duration_ms=duration_ms,
            average_fps=average,
            guessed_fps=guessed,
            # A meaningful gap between the container's advertised rate and the
            # rate implied by the stream means the timeline cannot be treated
            # as uniform.
            variable_frame_rate=abs(average - guessed) > 0.01,
            header_frame_count=int(getattr(stream, "frames", 0) or 0),
            width=int(getattr(stream, "width", 0) or 0),
            height=int(getattr(stream, "height", 0) or 0),
        )


def fingerprint(frame) -> np.ndarray:
    """Reduce a frame to a small grayscale array for cheap comparison."""
    small = frame.reformat(
        width=FINGERPRINT_SIZE,
        height=FINGERPRINT_SIZE,
        format="gray",
    )
    return small.to_ndarray().astype(np.int16)


def change_score(previous: np.ndarray | None, current: np.ndarray) -> float:
    """Normalised mean absolute difference, 0.0 (identical) to 1.0."""
    if previous is None:
        return 1.0
    if previous.shape != current.shape:
        return 1.0
    return float(np.abs(current - previous).mean() / 255.0)


def _digest(fp: np.ndarray) -> str:
    return hashlib.sha256(fp.astype(np.uint8).tobytes()).hexdigest()


def sample_frames(
    path: Path,
    *,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    max_samples: int | None = None,
) -> Iterator[FrameSample]:
    """Walk the video, yielding frames worth looking at.

    Hybrid strategy: a periodic sample so a static screen is still recorded,
    plus an immediate sample whenever the picture changes enough, plus a short
    burst of denser sampling after a change so a transition is not straddled.

    Decoding is streamed and only the tiny fingerprint is held, so memory does
    not grow with the length of the recording.
    """
    av = _import_av()
    if interval_ms <= 0:
        raise ValueError("interval must be positive")

    emitted = 0
    previous: np.ndarray | None = None
    next_due_ms = 0
    burst_until_ms = -1

    with av.open(str(path), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError("media contains no video stream")
        stream.thread_type = "AUTO"

        for frame in container.decode(video=0):
            if frame.time is None:
                continue
            timestamp_ms = int(frame.time * 1000)

            in_burst = timestamp_ms <= burst_until_ms
            due = timestamp_ms >= next_due_ms
            if not due and not in_burst:
                continue

            current = fingerprint(frame)
            score = change_score(previous, current)
            changed = score >= change_threshold

            if not (due or changed):
                continue

            yield FrameSample(
                timestamp_ms=timestamp_ms,
                change_score=score,
                digest=_digest(current),
                keyframe=bool(frame.key_frame),
            )
            emitted += 1
            previous = current

            if changed:
                burst_until_ms = timestamp_ms + BURST_DURATION_MS
                next_due_ms = timestamp_ms + BURST_INTERVAL_MS
            else:
                next_due_ms = timestamp_ms + interval_ms

            if max_samples is not None and emitted >= max_samples:
                return


def group_observations(
    samples: list[FrameSample],
    *,
    duration_ms: int,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> list[ObservationSpan]:
    """Collapse consecutive similar samples into spans.

    A slide held for forty seconds becomes one observation, not forty. Without
    this a long workshop produces thousands of near-identical rows and the
    screen track stops being useful.
    """
    if not samples:
        return []

    spans: list[ObservationSpan] = []
    start = samples[0]
    members = 1
    peak = start.change_score

    for index in range(1, len(samples)):
        sample = samples[index]
        if sample.change_score >= change_threshold:
            spans.append(
                ObservationSpan(
                    start_ms=start.timestamp_ms,
                    end_ms=sample.timestamp_ms,
                    representative_frame_ms=start.timestamp_ms,
                    change_score=round(peak, 6),
                    content_hash=start.digest,
                    frames_sampled=members,
                )
            )
            start = sample
            members = 1
            peak = sample.change_score
            continue

        members += 1
        peak = max(peak, sample.change_score)

    spans.append(
        ObservationSpan(
            start_ms=start.timestamp_ms,
            end_ms=max(duration_ms, start.timestamp_ms),
            representative_frame_ms=start.timestamp_ms,
            change_score=round(peak, 6),
            content_hash=start.digest,
            frames_sampled=members,
        )
    )
    return spans


def frame_at(path: Path, timestamp_ms: int) -> "tuple[bytes, int]":
    """Return (PNG bytes, actual timestamp_ms) for the frame at a timestamp.

    Seeks rather than decoding from the start, so retrieving a frame four hours
    into a recording does not cost four hours of decoding. The returned
    timestamp is the frame actually found, which may differ slightly from the
    one asked for; the caller is told rather than misled.
    """
    av = _import_av()
    if timestamp_ms < 0:
        raise ValueError("timestamp must not be negative")

    with av.open(str(path), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError("media contains no video stream")
        stream.thread_type = "AUTO"

        target_seconds = timestamp_ms / 1000.0
        offset = int(target_seconds / float(stream.time_base))
        try:
            container.seek(offset, stream=stream, backward=True, any_frame=False)
        except Exception:
            container.seek(0)

        chosen = None
        for frame in container.decode(video=0):
            chosen = frame
            if frame.time is not None and frame.time >= target_seconds:
                break
        if chosen is None:
            raise ValueError("no frame available at that timestamp")

        actual_ms = int((chosen.time or 0) * 1000)
        return _encode_png(chosen), actual_ms


def _encode_png(frame) -> bytes:
    """Encode a single frame as PNG.

    Uses a bare codec context rather than a container: the image2 muxer refuses
    a file-like target, and a single still image needs no muxer at all.
    """
    av = _import_av()
    context = av.CodecContext.create("png", "w")
    context.width = frame.width
    context.height = frame.height
    context.pix_fmt = "rgb24"

    packets = context.encode(frame.reformat(format="rgb24"))
    packets += context.encode(None)
    return b"".join(bytes(packet) for packet in packets)
