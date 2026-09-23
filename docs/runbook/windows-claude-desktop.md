# Windows alpha runbook — Claude Desktop

This runbook gets the first functional Vorquel Watch alpha running locally.

## What this alpha does

- local file ingest from the CLI;
- content SHA-256 dedupe;
- local raw-media storage;
- structured metadata/transcript persistence in the dedicated Supabase project vorquel-content-brain;
- local Faster-Whisper transcription in FAST mode;
- Claude Desktop access through a local stdio MCP server;
- transcript FTS, timestamped segment retrieval and controlled exports;
- reviewed learning: propose → human approval/rejection → approved knowledge FTS.

Raw media is not uploaded to Supabase by this alpha.

## 1. Setup

From PowerShell in the repository root:

powershell -ExecutionPolicy Bypass -File .\scripts\windows\setup.ps1 -SupabaseUrl https://xnygwzuckijaxzlszfpn.supabase.co

The script asks for the Supabase secret/service key with hidden input. The key is written only to the local Vorquel Watch data directory and is ignored by Git.

The first transcription may download the configured Faster-Whisper model into the local Vorquel Watch model cache. Media itself is not sent to that model host.

## 2. Claude Desktop MCP

Setup writes a generated snippet to:

%LOCALAPPDATA%\VorquelWatch\claude-mcp.json

Merge its mcpServers.vorquel-watch entry into Claude Desktop MCP configuration without deleting existing servers. Restart Claude Desktop.

No Supabase secret is placed in Claude's MCP configuration. The local MCP process reads the local control-plane configuration file.

## 3. Start worker

Keep scripts\windows\start-worker.ps1 running while jobs are processed.

## 4. Ingest media

The path is accepted by the local CLI only; it is never an MCP argument.

.\.venv\Scripts\vorquel-watch.exe ingest C:\path\to\video.mp4

Copy the returned source_id.

## 5. Ask Claude

Example: Use Vorquel Watch to analyze source src_... . Start FAST analysis if needed, then tell me the main topics with timestamps.

Claude should call get_capabilities -> get_source -> start_analysis -> get_job -> search_transcript -> get_segment.

## 6. Teach the Vorquel Brain

After Claude has grounded itself in transcript/screen evidence, ask:

"Analise esta source e me mostre o que vale aprender. Não salve nada ainda."

Claude may create PENDING candidates with `propose_knowledge_candidate` and then
show them to you with provenance/timestamps. Candidate text is still untrusted
data and has no instruction authority.

Only after an explicit human instruction such as:

"Salve os candidatos 1, 3 e 5."

may Claude call `approve_knowledge_candidate` for those exact candidate IDs.
Use `reject_knowledge_candidate` for items you do not want.

Later, ask:

"O que o Cérebro Vorquel já aprendeu sobre retry no n8n?"

Claude should use `search_knowledge`. Approved knowledge is reusable reference
material, not policy or canonical truth.

## 7. Diagnostics

## 7. Diagnostics

.\.venv\Scripts\vorquel-watch.exe doctor

## Alpha limits

STANDARD alignment and SPEAKERS diarization are not yet implemented. URL ingest, remote MCP, pgvector/RAG and raw-media cloud upload are also disabled. Human approval is mandatory for knowledge promotion; source content cannot approve itself.
