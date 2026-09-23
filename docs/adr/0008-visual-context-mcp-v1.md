# ADR 0008 — Visual Context MCP V1

Status: Accepted
Date: 2026-09-20
MCP version: V1.3

## Context and supersession

ADR 0003 froze the original V1 MCP surface at 14 tools and explicitly withheld
dedicated screen/frame tools until a later architecture decision. ADR 0004
authorized five reviewed-knowledge tools in V1.1, and ADR 0005 authorized three
knowledge lifecycle/synthesis tools in V1.2. The effective surface before this
decision is therefore 22 tools.

Visual Review Core V1 now provides deterministic, bounded frame selection, and
Visual Review Pack V1 composes those selected frames with existing OCR,
screen-observation and transcript evidence. ADR 0007 deliberately left public
MCP exposure to a separate decision.

This ADR is that decision. It partially supersedes ADR 0003 only for aggregated
visual-context retrieval and resolves the future decision identified by ADR
0007. It does not authorize a `get_frame` tool or alter ADRs 0004/0005.

## Decision

MCP V1.3 adds exactly two read-only tools:

- `get_visual_context_at`
- `get_visual_context_range`

The surface grows from 22 to exactly 24 tools. Existing tools and their
semantics remain unchanged. `get_frame` remains an internal service method and
is not registered as an MCP tool.

### Point schema

```
get_visual_context_at(
  source_id: str,
  timestamp_ms: int,
  mode: "efficient" | "balanced" | "detailed" = "balanced"
)
```

The point query selects inside a one-second focused window centered on the
requested absolute timestamp (500 ms on each side, clipped to the timeline),
then returns the selected frame closest to the requested PTS as one safe
VisualReviewPack projection.

### Range schema

```
get_visual_context_range(
  source_id: str,
  start_ms: int,
  end_ms: int,
  mode: "efficient" | "balanced" | "detailed" = "balanced",
  max_packs: int = 20
)
```

The range is focused and retains absolute media timestamps. Packs are ordered
ascending by timestamp.

## Access and readiness

The only locator accepted is a canonical opaque `source_id`, validated by the
existing `validate_id` boundary. No tool schema accepts a path, filename,
directory, workspace, URL, URI, command, credential or storage locator.

The source must:

- exist in the Watch source registry;
- have `ingest_status = READY`;
- contain video and a positive known duration;
- point to a successful job whose `source_id` matches;
- pass content-addressed local integrity verification.

Failure never starts a job, worker, OCR or ASR pass. It returns a sanitized
error such as `SOURCE_NOT_FOUND`, `SOURCE_NOT_READY`, `INVALID_TIMESTAMP`,
`INVALID_RANGE`, `RANGE_TOO_LARGE`, `INVALID_MODE`, `TOO_MANY_PACKS`,
`MEDIA_UNAVAILABLE` or `VISUAL_CONTEXT_UNAVAILABLE`.

## Reuse and read-only flow

```
source_id
  -> registered source + successful job check
  -> controlled content-addressed object verification
  -> Visual Review Core focused selection
  -> existing observation/OCR/transcript read model
  -> Visual Review Pack composition
  -> canonical MCP envelope and safe projection
```

The MCP methods call the existing `extract_frames` and
`build_visual_review_pack(s)` APIs. They do not duplicate selection,
deduplication, budgets, OCR or transcript alignment.

Repository access is read-only: `get_source`, `get_job`, `observation_at`,
`screen_text_blocks` and `segments_in_range`. No VisualReviewPack is persisted,
and no source, transcript, observation, artifact or knowledge record is
created or changed.

## Resource bounds

- Modes are allowlisted to `efficient`, `balanced`, `detailed`.
- A point query decodes at most a one-second focused window and asks Core for
  at most three selected frames before choosing the closest.
- A range must be positive and no longer than **600,000 ms (10 minutes)**.
- `max_packs` defaults to **20**, is at least **2** so first/last temporal
  coverage survives, and is at most **50**.
- The requested range must fit within the known source duration.
- Visual Review Core's lower internal hard caps still apply.

Ten minutes matches the Core's documented long-video boundary: scene-aware
selection decodes every frame, and beyond that point synchronous MCP work
becomes a sparse, CPU-heavy scan better split into focused requests. Fifty is
half the normal balanced Core cap and follows existing MCP conventions that
bound result lists to tens of items.

## Response and trust boundary

Responses use the existing canonical MCP envelope. Payloads contain only
opaque IDs, hashes, dimensions, timestamps, selection reasons, permitted OCR
and transcript text, and explicit provenance. They never contain image bytes,
`image_bytes`, host paths, temp/workspace paths or raw database rows.

Every projected pack remains:

```
data_trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

The outer MCP security envelope also declares that payload content cannot
select tools or change policy. OCR/transcript strings such as “call shell” or
“read ~/.ssh/id_rsa” remain inert text. No tool invocation, filesystem lookup
or network access is derived from their content.

## Explicit absences

- No `get_frame` and no binary/image transport.
- No caller-supplied path or URL.
- No arbitrary filesystem access; only the already controlled, hash-verified
  object path derived from registered source metadata is opened by Core.
- No network, LLM, vision model, download, OCR or ASR invocation.
- No persistence or knowledge promotion.
- No embeddings, vector search, RAG, agent, browser, UI or remote/HTTP MCP.

## Alternatives rejected

- **Expose old `get_context_at` / `get_context_range` methods.** They predate
  Core/Pack, return a different shape and do not constitute the explicit V1.3
  contract. Their meaning remains unchanged and they stay unregistered.
- **Expose `get_frame`.** It expands the contract into image transport and was
  explicitly not authorized.
- **Accept a path to avoid source lookup.** That bypasses Source Guard,
  integrity verification and the opaque-ID boundary.
- **Allow arbitrary or eight-hour ranges.** Output caps do not bound decode
  cost; scene-aware selection must inspect the requested timeline.
- **Persist packs.** They are deterministic evidence projections, not truth or
  knowledge state.

## Risks

- Scene-aware modes consume CPU proportional to range duration even though
  output is capped.
- Each selected frame performs bounded evidence reads; the 50-pack ceiling
  limits query amplification.
- A successful job may legitimately have no OCR or transcript evidence for one
  instant; the pack represents absence rather than triggering new processing.
- The runtime still holds the broader `service_role` credential, an existing
  security gap recorded in `SECURITY.md`; these tools nevertheless call only
  the narrow read methods listed above.

Any future image transport, larger range, remote MCP or knowledge promotion
requires another explicit architecture decision.
