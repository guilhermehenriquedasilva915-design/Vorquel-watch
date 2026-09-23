# ADR 0002 — Screen pipeline and the MCP surface extension

Status: Superseded in part by ADR 0003
Date: 2026-09-19

The screen/OCR pipeline remains useful. The proposed five-tool MCP extension and
URL ingest do not ship in V1 because the user explicitly reaffirmed the frozen
MCP surface and deferred URL ingest. See ADR 0003.

## Context

V1 answered "what was said". A recorded workshop also shows things: a dashboard,
a terminal, a slide, a workflow canvas. A transcript alone cannot answer "what
was on screen when he said that", which is the question the product exists to
answer.

The V1 MCP surface was deliberately frozen at 14 tools. Extending it is an
architecture decision and needs recording rather than quietly happening.

## Decision

Add a screen pipeline and five MCP tools.

### Pipeline

```
video -> frame timeline (PTS)
      -> change detection
      -> representative frames
      -> OCR
      -> screen_observations (+ screen_text_blocks)
```

An observation is a span during which the screen did not meaningfully change,
not a frame. A slide held for 40 seconds is one row. This is what makes a
five-hour workshop tractable: the row count follows how often the screen
changes, not the frame rate.

Frames are addressed by timestamp and materialised on demand from the media
file. No frame images are persisted, and no per-frame rows exist. A five-hour
recording at 30fps is over half a million frames; indexing them is neither
necessary nor affordable.

Timestamps come from PTS via PyAV (`frame.time`), never from `frame_number /
assumed_fps`, which is wrong for variable frame rate content.

### MCP tools added

`get_video_info`, `search_screen_text`, `get_frame`, `get_context_at`,
`get_context_range`. Fourteen tools become nineteen.

### Constraints that do not change

- No tool accepts a filesystem path or an arbitrary URL. A YouTube URL enters
  only through the controlled ingest path in the local control plane, never as
  an MCP argument.
- Identifiers remain opaque and validated.
- Screen text is `UNTRUSTED_DERIVED` with `instruction_authority = NONE`,
  enforced by CHECK constraints, not by convention. Text read from a screen
  saying "ignore all previous instructions" is content, exactly like a spoken
  sentence. The same holds for code, terminal output, URLs and QR codes that
  appear on screen. None of it is executed, fetched or obeyed.
- Provenance is enforced by composite foreign keys, so an observation cannot
  reference a source different from its job's.
- Screen search is gated on a successful job, like transcript search.

## Frame delivery to Claude

`get_frame` returns `ImageContent` — `type="image"`, base64 `data`, `mime_type`
— which is the content block the pinned MCP SDK defines. `annotations.audience`
marks whether a frame is for the model or the operator. This is the SDK's own
interface, confirmed by introspecting the installed package rather than assumed.

Frames are encoded by PyAV, which has png, mjpeg and webp encoders, so frame
delivery adds no image dependency.

## OCR engine

RapidOCR 3.9.2 on onnxruntime, chosen by measurement rather than reputation.

| Fixture | Recall | Time |
|---|---|---|
| Dashboard UI, pt+en | 7/7 | 976 ms |
| Terminal and code | 7/7 | 1681 ms |
| Accented Portuguese | 13/13 tokens, 5/5 full lines | 1297 ms |

It read `createClient(url, key)`, `Webhook -> Normalize Lead -> Supabase`,
`Integração`, `Configurações`, `atenção à inscrição`, and an em dash. It runs on
onnxruntime, which faster-whisper already installs, so the runtime was already
present.

Caveat recorded honestly: these fixtures are synthetic renders with clean text.
Real video frames carry compression artifacts, scaling and motion blur.
Accuracy on real workshop footage is unverified until the acceptance test runs.

Follow-up (2026-09-21): real OCR and screen-observation evidence was exercised
through the subsequently authorized Visual Context MCP V1.3 surface. The
result is recorded in `docs/acceptance/real-visual-context-e2e-v1.md`. This
follow-up does not revive the five-tool proposal or `get_frame` from this
historical ADR.

Model artifacts are pinned: the upstream config fixes each model URL to tag
`v3.9.2` and declares a SHA256 per model, and `Global.model_root_dir` allows a
local model directory. This matches the pinning discipline already applied to
the transcription model.

## Alternatives rejected

- **A vision model over every frame.** Cost and latency are not justified when
  the question is "what text was on screen", and it would put a second model in
  the hot path of a five-hour recording.
- **Persisting a row per frame.** Half a million rows per recording to answer
  questions that span seconds.
- **Tesseract.** Needs a system binary install, which is a manual step on the
  operator's machine, and the ONNX runtime was already present.
- **Storing frame images.** Frames are reproducible from the source media by
  timestamp; storing them duplicates evidence and multiplies retention cost.

## Consequences

- Two new tables, one new search function, five new MCP tools.
- Two new optional dependencies, each in its own extra: `screen` (rapidocr) and
  `youtube` (yt-dlp). A transcript-only install carries neither.
- YouTube ingest introduces the first outbound network fetch in the product, so
  it carries its own constraints: HTTPS only, YouTube hosts only, no playlists,
  no cookies, `--ignore-config`, and size, duration and time limits.
- OCR accuracy on real footage, and the cost of the screen pipeline on a long
  recording, remain to be measured.
