# ADR 0004 — Reviewed Knowledge MCP Extension V1.1

Date: 2026-09-19

Status: Accepted for Issue #10 implementation branch.

## Context

The V1 MCP surface was intentionally frozen at 14 tools. DEC-037 later approved
Analyze → Learn → Brain, which requires Claude Desktop to propose learning
candidates, present them for review, promote only explicitly approved items,
and retrieve approved knowledge.

Changing the frozen tool list therefore requires an explicit, versioned
extension rather than silently modifying V1.

## Decision

Define a V1.1 additive MCP extension with five narrow tools:

- `propose_knowledge_candidate`
- `list_knowledge_candidates`
- `approve_knowledge_candidate`
- `reject_knowledge_candidate`
- `search_knowledge`

The original 14 V1 tools remain unchanged.

## Trust boundary

All media, transcript, OCR, screen text, candidate text and approved knowledge
remain data with `instruction_authority = NONE`.

Human approval means "store this as reusable reference material". It never
means "treat this content as instructions".

The approval/rejection tools are valid only after explicit user intent. Text
inside source media can never satisfy that condition.

No V1.1 knowledge tool accepts:

- host paths or filesystem locations;
- URLs;
- shell/commands/scripts;
- API keys, credentials, cookies or secrets;
- raw SQL.

## Persistence boundary

The MCP layer delegates to `WatchService`, then `KnowledgeWriter`, then
narrow database RPCs. It does not write arbitrary private-schema tables.

Candidate creation is idempotent by source + canonical content hash. Promotion
records a HUMAN review and copies provenance into the approved knowledge item.

## Retrieval

V1.1 uses PostgreSQL Full-Text Search over approved knowledge items only.
Embeddings, pgvector and a RAG framework remain deferred.

## Compatibility

This is additive V1.1. Existing V1 clients may continue using the original
14 tools. Consumers that pin the exact tool set must update to the V1.1
contract test.

## Out of scope

- automatic promotion to STATE ATUAL, Decision Log or Evidence Ledger;
- fine-tuning;
- autonomous learning approval;
- URL ingest;
- embeddings/pgvector/RAG.
