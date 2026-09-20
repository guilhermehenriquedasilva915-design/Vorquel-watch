# ADR 0007 — Visual Review Pack V1

Status: Accepted
Date: 2026-09-20

## Context

Visual Review Core V1 deterministically selects bounded frames and records an
absolute PTS, reason, opaque frame/artifact identifiers, payload hash and trust
metadata for each result. Separately, the existing Watch pipeline persists
screen-observation spans, OCR text blocks and transcript segments on the same
source timeline.

A later consumer needs these existing facts assembled around a selected frame
without rerunning OCR, interpreting pixels, invoking a model or creating a new
truth/knowledge record.

## Decision

Add `vorquel_watch.visual_review_pack`, a small read-only compositor with two
internal Python entry points:

- `build_visual_review_pack` composes one selected frame;
- `build_visual_review_packs` composes and orders a collection, with optional
  absolute `start_ms` / `end_ms` filtering.

The compositor accepts `SelectedFrame` values from Visual Review Core and an
evidence reader matching three existing `WatchRepository` methods:

- `observation_at`;
- `screen_text_blocks`;
- `segments_in_range`.

No new repository, service, MCP, table or migration is introduced.

```
SelectedFrame (Visual Review Core)
        +
screen_observation containing the frame PTS
        +
persisted OCR blocks for that observation
        +
transcript segments overlapping the configured window
        ↓
VisualReviewPack (deterministic evidence composition)
```

## Transcript alignment

The default window is 2,000 ms before and 3,000 ms after the selected frame.
The lower bound is clamped to zero. A segment is included when its closed
interval overlaps the pack window:

```
segment.end_ms >= window.start_ms
and segment.start_ms <= window.end_ms
```

`WatchRepository.segments_in_range` already implements that query. The
compositor repeats the overlap check as defense in depth, sorts results by
`(start_ms, end_ms, segment_id)`, copies the canonical `effective_text`
verbatim, and never summarizes, rewrites or infers. No transcript is a valid
result with `segments = []`.

Focused range filtering never rebases timestamps. Pack, frame, observation and
segment timestamps remain absolute media PTS values.

## Screen observation alignment

Screen observations are contiguous, non-overlapping spans in the existing
pipeline. `observation_at(source_id, timestamp_ms)` therefore identifies the
closest applicable observation: the one whose span contains the selected frame
PTS. Its persisted `ocr_text` and ordered `screen_text_blocks` are projected
into the pack. No OCR, frame extraction or screen analysis runs here.

If no observation contains the PTS, the pack remains valid with a null
observation and empty text blocks.

## Provenance

Each pack links the existing identifiers for:

- source;
- selected frame;
- rendered frame artifact;
- screen observation;
- OCR text blocks;
- transcript segments.

Frame hashes, dimensions and absolute timestamps are retained. The compositor
does not mint a second identity for any existing entity. It rejects a frame,
observation, OCR block or transcript segment whose `source_id` differs from the
requested source, preventing cross-source evidence composition.

## Path and field security

Serialization uses explicit allowlists. It excludes frame bytes, storage
locations, workspace/temp paths, arbitrary database columns and transcript
`raw_text`. The repository's canonical reviewed surface, `effective_text`, is
used for transcript content.

Untrusted OCR or transcript text is preserved verbatim. Such text may itself
contain a string that resembles a local path or a command; that string remains
content and never becomes a path argument or filesystem operation. No
filename metadata is accepted by this API.

## Trust boundary

Every pack and every projected OCR/transcript row is emitted as:

```
data_trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

Text such as “ignore previous instructions”, “call tool X” or “read
C:\Users\...” is data. The pack cannot choose tools, change policy, authorize
actions or trigger I/O.

A Visual Review Pack is not knowledge, is not truth state and is not an
instruction. It is a composition of traceable evidence.

## Deliberate absences

- No LLM or visual model.
- No network access.
- No new OCR or visual analysis.
- No media/file opening in the compositor.
- No MCP tool or MCP contract change.
- No persistence, migration or new database.
- No frame reselection, deduplication or budget change.

## Alternatives rejected

- **Put composition in the MCP service.** That would prematurely create a
  public tool and couple an internal evidence type to the frozen MCP surface.
- **Persist packs.** Packs are reproducible views of canonical evidence;
  persistence would create another state lifecycle and a misleading truth
  record.
- **Rerun OCR at each selected frame.** This duplicates expensive work and can
  disagree with the already persisted observation evidence.
- **Summarize transcript/OCR.** That introduces inference and destroys exact
  evidence fidelity.
- **Pass arbitrary dictionaries through.** Explicit projections are required
  to prevent future columns or local paths from leaking into serialized packs.

## Risks and consequences

- Composition currently performs bounded repository reads per frame. A large
  pack set may cause many small queries, although Visual Review Core caps the
  frame count.
- Repository projections cap a single frame window at 200 transcript segments
  and 200 OCR blocks. The default five-second transcript window makes this a
  defensive operational bound, not ordinary truncation.
- A frame falling in a true gap between persisted observation spans receives
  no screen/OCR evidence rather than a guessed association.
- Content remains only as reliable as the upstream ASR and OCR evidence; the
  pack makes no semantic correctness claim.

## Next steps

Any future visual-model analysis, public MCP surface, UI, knowledge proposal,
export or persistence requires a separate decision. This ADR authorizes only
the internal deterministic evidence compositor.
