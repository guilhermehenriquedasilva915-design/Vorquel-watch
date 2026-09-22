# Analyze-to-Candidate Draft V1

Analyze V1 is an internal, bounded bridge from already processed Watch
evidence to ephemeral drafts for human review. It does not add an MCP tool,
database table, provider SDK or automatic learning path.

## Flow

```text
READY source + SUCCEEDED job
  -> bounded transcript segments + bounded VisualReviewPacks
  -> explicit character/evidence budget
  -> SemanticAnalyzer protocol
  -> validated, anchored, deduplicated DraftCandidate values
  -> local UI review/edit/discard
  -> explicit human propose-draft action
  -> existing KnowledgeWriter -> PENDING candidate
  -> existing review/approval lifecycle
```

Analyze itself performs no write. A draft is an in-memory Python value, not a
new persisted model. The explicit propose action reuses `KnowledgeWriter` and
the existing `knowledge_candidates` / `knowledge_sources` contract.

## Provider strategy

`SemanticAnalyzer.analyze_evidence(context)` is the provider boundary. The V1
runtime uses a deterministic local heuristic and the tests inject a fake. No
OpenAI, Anthropic, Gemini or other paid API is required or called. A future
provider may implement the protocol without changing evidence selection,
validation, deduplication or persistence boundaries.

## Context budget

One Analyze run is limited to:

- 120,000 ms of the source timeline;
- 24 transcript segments;
- 8 VisualReviewPacks selected with the existing efficient Core mode;
- 2,000 characters per evidence item;
- 12,000 characters in the analyzer context;
- 5 accepted drafts.

The two-minute range follows the existing service's 120-second bounded-context
ceiling and is deliberately much smaller than Visual Context's ten-minute
read-only maximum. Twenty-four segments cover a dense two-minute transcript at
roughly five seconds per segment. Eight packs are below the public default of
twenty and bound both decoding and repository fan-out. The 12,000-character
ceiling matches the existing candidate-summary bound and keeps V1 conservative.
All limits are centralized in `AnalyzeBudget` and can be lowered by tests or a
future trusted caller.

## Provenance and trust

Every accepted draft has at least one same-source structured evidence ref.
Ephemeral refs may include frame/artifact IDs; proposal converts them to the
already supported transcript, segment, observation and absolute timestamp
fields. No schema change is required.

Every draft remains:

```text
trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

Analyzer output is validated against existing knowledge types and epistemic
statuses. It cannot promote status implicitly. Unknown/malformed/unanchored
output is discarded. Prompt-injection-shaped evidence stays data and the local
heuristic does not turn it into a draft or execute it.

## Human boundary

The local UI accepts an opaque processed `source_id`, shows drafts and their
evidence, allows editing or discarding, and requires an explicit confirmation
before invoking `brain propose-draft`. That command creates only a PENDING
candidate. Approval remains a separate existing action.

MCP V1.3 remains unchanged at 24 tools. `get_frame` remains unexposed.
