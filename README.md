# Vorquel Watch

Private implementation repository for Vorquel Watch / Content Brain.

## Current state

The repository contains the V1 security/architecture baseline plus a first functional alpha for local media ingest, FAST transcription and Claude Desktop MCP access.

### Alpha data flow

Claude Desktop -> local MCP -> Supabase structured data

Local CLI -> Source Guard -> content-addressed local media

Local worker -> Faster-Whisper -> transcript segments -> Supabase

Raw video/audio stays local in this alpha.

## Quick start on Windows

1. Read docs/runbook/windows-claude-desktop.md.
2. Run scripts/windows/setup.ps1 with the dedicated Supabase project URL.
3. Paste the Supabase secret/service key only into the hidden local setup prompt.
4. Start scripts/windows/start-worker.ps1.
5. Merge the generated Claude MCP snippet into Claude Desktop and restart it.
6. Ingest a local media file with vorquel-watch ingest.
7. Ask Claude to analyze the returned source_id.

## Security baseline

- Media-derived content has instruction_authority=NONE.
- MCP accepts opaque IDs, never arbitrary local paths or arbitrary URLs.
- No unrestricted shell/filesystem tool exists.
- Supabase server credentials remain local to the backend/control plane.
- Raw media is never uploaded to Supabase in the alpha.
- Reviews are append-only at the database layer.
- GitHub Actions are pinned by commit SHA.

Do not redefine frozen schemas, MCP permissions, trust boundaries or processing modes without an explicit architecture decision.
