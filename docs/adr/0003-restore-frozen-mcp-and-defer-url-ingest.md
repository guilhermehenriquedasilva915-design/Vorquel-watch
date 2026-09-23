# ADR 0003 — Restore the frozen V1 MCP surface and defer URL ingest

Status: Accepted
Date: 2026-09-19

## Context

The production-readiness branch temporarily grew from the frozen 14-tool MCP
contract to 19 tools and exposed a local YouTube ingest command. The current V1
instructions explicitly require both constraints to remain frozen:

- no new MCP tools without a new explicit architecture decision;
- no URL/YouTube/Instagram ingest in the main V1 delivery.

The screen/OCR implementation itself remains useful and does not require either
contract expansion.

## Decision

1. The V1 MCP server exposes exactly the frozen 14 tools.
2. Screen/OCR processing may run locally and persist bounded derived evidence,
   but dedicated screen/frame MCP tools are not exposed in V1.
3. V1 ingest accepts only a user-selected local audio/video file.
4. Network/URL ingest is deferred to V1.1 and requires its own Source Guard,
   redirect/DNS/IP policy, quotas and explicit architecture approval.
5. The Windows setup installs the hash-locked local transcription + screen/OCR
   runtime and points to the dedicated `vorquel-watch` Supabase project.
6. No raw media is uploaded to Supabase or an external model service.

## Consequences

The branch loses convenience features that were added before the freeze was
re-checked, but returns to the approved trust boundary. The screen track remains
available for the future local UI/control plane. Claude remains transcript-first
until an explicit MCP schema decision is made.
