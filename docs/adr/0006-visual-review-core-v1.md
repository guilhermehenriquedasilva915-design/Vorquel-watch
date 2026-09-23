# ADR 0006 — Visual Review Core V1

Status: Accepted
Date: 2026-09-20

## Context

The existing screen pipeline uses PyAV presentation timestamps, periodic
sampling, visual change detection and RapidOCR to produce bounded screen
observations. It deliberately does not retain every decoded frame. That track
is useful for text, but a future visual reviewer also needs representative
pixels for diagrams, interfaces, tables, code and other spatial information.

This decision adds only the deterministic frame-selection core. It does not
add a vision model, a new MCP surface, knowledge extraction or automatic
learning.

The MIT-licensed `bradautomates/claude-video` project was reviewed as a
technical reference. Its scene/keyframe modes, uniform under-production
fallback, last-kept-frame deduplication, duration-aware budgets, focused ranges
and pinned timestamps informed this decision. No source code or runtime
dependency was copied into Vorquel Watch.

## Decision

Add `vorquel_watch.visual_review` as an additive layer over the existing
`frames.py` primitives and PyAV runtime.

```
verified local video
  -> PTS-bounded decode
  -> scene changes | keyframes
  -> uniform fallback when sparse or failed
  -> merge explicit transcript-cue timestamps
  -> perceptual dedup against last kept frame
  -> even temporal cap (first, last and pinned survive)
  -> render selected PNGs at configured width
  -> opaque frame/artifact metadata + untrusted-data labels
```

### Selection

- `efficient` asks the PyAV/FFmpeg decoder to discard non-key frames with
  `skip_frame = NONKEY`, then performs short normal seeks for exact range
  boundaries. It is the lower-cost scan mode.
- `balanced` is the default and keeps changes whose 64×64 grayscale mean
  absolute difference crosses `0.06`.
- `detailed` lowers the scene threshold to `0.04` and uses a denser budget.
- All modes retain temporal boundaries. If the primary detector fails or
  produces fewer than the profile minimum, a uniform PTS sampler fills the
  range. Detector failure therefore degrades coverage rather than failing the
  review.

Candidate timestamps and focused-range results are absolute media PTS values,
never frame-number estimates.

### Deduplication

Candidates are ordered by timestamp and compared to the last frame that was
kept, using a 64×64 grayscale fingerprint. The default near-duplicate
threshold is `1.5 / 255`, intentionally much lower than the screen-observation
grouping threshold. This reduces held-screen repetition while retaining small
changes. First, last and explicitly pinned frames are never removed.

Deduplication fails open: a comparison error keeps the candidate. Result
metrics are staged and unambiguous:

- `candidate_count`: unique candidates before deduplication;
- `deduplicated_count`: candidates remaining after deduplication;
- `kept_count`: rendered frames remaining after the budget cap.

### Budgets

All values live in the exported `BUDGET_PROFILES` table.

| Mode | normal cap | focused cap | normal interval | focused interval |
|---|---:|---:|---:|---:|
| efficient | 40 | 80 | 8000 ms | 1000 ms |
| balanced | 100 | 160 | 4000 ms | 500 ms |
| detailed | 200 | 300 | 2000 ms | 250 ms |

The target count is duration-aware and never exceeds the applicable cap. A
caller may lower, but not raise, the central hard cap. Capping is spread evenly
across time and happens after deduplication. First and last coverage and pinned
timestamps take priority. If pinned timestamps plus required boundaries cannot
fit, the core reports the conflict explicitly instead of silently dropping
requested evidence.

### Focused ranges and transcript cues

`start_ms` and `end_ms` restrict decoding to a denser window while retaining
absolute timestamps. Explicit `timestamps_ms` inside that window are assigned
`TRANSCRIPT_CUE`, pinned through deduplication and given priority in the cap.
The core does not inspect or interpret transcript text.

### Resolution and storage

The default output width is 512 pixels; callers may select 128–2048 pixels.
Aspect ratio is preserved. The cap prevents accidental large-output growth.

Thumbnails and rendered PNG bytes are in memory by default. If a trusted local
caller supplies an output workspace, only final selected PNGs are written,
named by opaque artifact IDs. No source path is emitted in external metadata,
no source media is deleted, and no new retention policy or cloud storage is
introduced. Callers that use a temporary workspace own its lifecycle.

### Provenance and trust boundary

Every selected frame carries an opaque deterministic frame ID, source ID,
absolute timestamp, selection reason, dimensions, artifact ID and artifact
SHA-256. The artifact payload is the rendered PNG; IDs include the source and
payload digest so equal timestamps from different sources cannot collide.

Every frame is always:

```
data_trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

Pixels, OCR-like text, QR codes, terminal commands and statements such as
“ignore previous instructions” remain data. This core never executes them,
uses them for tool selection or treats them as policy.

## Compatibility and consequences

- No schema migration is required.
- No dependency is added; PyAV and NumPy are already in the media runtime.
- The worker, transcript, OCR, Brain, CLI, MCP, UI and Obsidian paths are not
  changed. Integration into a later visual-review workflow is additive.
- Scene-aware selection decodes every frame in its requested range. This is
  deterministic and bounded in output, but CPU cost still follows video
  duration. Focused ranges avoid reprocessing the entire file.
- Grayscale mean-difference deduplication is intentionally simple. It cannot
  understand semantic importance and may be sensitive to compression noise or
  very localized pixel changes; the low threshold and fail-open rule reduce
  evidence-loss risk.

## Deferred to Prompt 2

Visual-model analysis, Anthropic/OpenAI/Gemini integration, an image MCP or
`get_frame` tool, a Visual Review Pack, visual knowledge/automatic learning,
Context Pack changes, embeddings/vector search, graph databases, agent loops,
new downloaders/sources, browser work, web UI and automatic Obsidian ingestion
remain explicitly out of scope.
