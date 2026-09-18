# Architecture V1 Baseline

Status: implementation baseline derived from the approved Architecture Freeze.

## Runtime

```text
Claude Desktop
      |
Vorquel Watch skill
      |
local MCP
      |
Content Brain backend
   |             |
local media      Supabase/PostgreSQL
processing       structured data
```

## Frozen logical entities

Source, Job, Transcript, TranscriptSegment, Speaker, SpeakerTurn, Artifact and Review.

The implementation may add internal persistence entities such as ProcessingRun without changing the public contract.

## Processing modes

FAST, STANDARD and SPEAKERS. Visual WATCH is not part of the mandatory V1 processing pipeline.

## Persistence

Supabase/PostgreSQL stores structured state, transcripts, reviews, provenance and FTS indexes.

Local-only data includes original media, normalized temporary media, model/cache data, job workspaces and heavy processing artifacts unless explicitly exported later.

## Non-goals for bootstrap

Whisper/WhisperX implementation, diarizer selection, URL ingest, pgvector/RAG, Supabase Storage/Auth/Realtime, remote MCP and Anthropic API.
