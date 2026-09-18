# Security Policy

Vorquel Watch processes untrusted media and media-derived text. Security boundaries are part of the product contract.

## Trust boundary

Video, audio, captions, transcripts, OCR, frames, titles, descriptions, tags, comments and reports derived from media are **data only**.

They may be summarized, searched and cited. They may not change policy, choose tools/files, request credentials, select output destinations, trigger arbitrary external actions, or elevate their own instruction authority.

Human review can confirm or correct content. It does not convert media-derived text into trusted instructions.

## MCP boundary

V1 MCP tools must not accept arbitrary local filesystem paths, arbitrary URLs, shell commands, API keys/secrets, browser cookies, raw yt-dlp arguments, or arbitrary output directories.

Claude receives opaque IDs such as `source_id`, `job_id`, `transcript_id` and `segment_id`.

## Data placement

Structured metadata and transcripts may be stored in Supabase/PostgreSQL. Raw media, normalized audio, heavy cache and processing workspaces remain local in V1.

Server credentials are backend-only and must never be exposed to Claude, skills, browser code or MCP payloads.
