# Vorquel Watch

Private implementation repository for Vorquel Watch / Content Brain.

Give it a workshop recording — a local file or a YouTube link — and it listens
to everything said, watches what happens on screen, reads the text that appears
there, and lines the two up on one timeline so Claude can answer questions about
both at once.

> "When he says this is the flow that receives the lead, the screen shows an n8n
> workflow containing Webhook, Normalize Lead, Supabase and Follow-up."

## Current state

| Area | State |
|---|---|
| Local file ingest | Implemented |
| YouTube ingest | Implemented, **never run against a real URL** |
| Transcription (FAST) | Implemented, **never run end to end** |
| Screen tracking, change detection, dedupe | Implemented, measured on synthetic fixtures |
| OCR | Implemented, measured on synthetic fixtures |
| Frame retrieval by timestamp | Implemented and measured |
| Combined speech + screen context | Implemented |
| MCP surface | 19 tools, contract-tested |
| Security and database integrity | Complete, verified against the live project |
| Long-video resume | Job-level recovery only; no mid-job resume |
| STANDARD / SPEAKERS / local UI | Not implemented |

Nothing has been run against a real recording yet: registration needs the
Supabase credential. Every claim above marked "measured" was measured on
synthetic fixtures, which is not the same thing.

### Data flow

```
local file ──┐
             ├─> Source Guard ─> content-addressed local storage
YouTube URL ─┘                            │
                                          ├─> Faster-Whisper ─> transcript segments
                                          └─> sample / change detect / OCR ─> screen observations
                                                          │
                          Claude Desktop ─> local MCP ─> structured data + frames
```

Raw media stays on the machine. Supabase holds structured metadata,
transcripts, screen observations and provenance — never media bytes, never host
paths.

## Setup on Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\setup.ps1
vorquel-watch configure --url https://xnygwzuckijaxzlszfpn.supabase.co
vorquel-watch doctor
.\scripts\windows\start-worker.ps1
```

`configure` takes the Supabase secret with hidden input and encrypts it with
DPAPI. It never lands in a readable file, the repository, or the Claude
configuration.

Merge the snippet from `%LOCALAPPDATA%\VorquelWatch\claude-mcp.json` into Claude
Desktop and restart it. The snippet carries no secret; the MCP process reads the
credential from the OS store itself.

Optional extras: `screen` (OCR) and `youtube` (yt-dlp). A transcript-only
install carries neither.

## Using it

```powershell
vorquel-watch ingest C:\path\to\workshop.mp4
vorquel-watch ingest-url https://www.youtube.com/watch?v=VIDEO_ID
```

Both return an opaque `source_id`. Paths and URLs are accepted here, in the
local control plane, and nowhere else — no MCP tool takes either.

Then ask Claude about that `source_id`. The questions it can answer:

| Question | Tools |
|---|---|
| What was said about X? | `search_transcript` |
| When does X actually appear on screen? | `search_screen_text` |
| What was said *and* shown at this moment? | `get_context_at` |
| What happened across this stretch? | `get_context_range` |
| Show me the screen at this timestamp | `get_frame` |

`search_transcript` and `search_screen_text` answer different questions: someone
can mention a tool without showing it, or show it without naming it. Comparing
them is how you find which tools were demonstrated rather than just discussed.

See `docs/runbook/workshop-analysis.md` for the full procedure.

### Credential management

```powershell
vorquel-watch credential status   # presence only, never the value
vorquel-watch credential rotate
vorquel-watch credential remove
```

## Accepted media

MP4, MKV/WebM, WAV, MP3, FLAC and OGG, with an audio track.

AVI is deliberately rejected. The MagicYUV heap overflow class of FFmpeg bugs
(CVE-2026-8461) is reached through the AVI, MKV and MOV demuxers, and AVI
carries the widest legacy codec surface for the least value here.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"
.\.venv\Scripts\python.exe scripts\security\check_repo.py
```

167 tests. Dependencies are locked with hashes — read `docs/dependencies.md`
before touching `pyproject.toml`.

## Limitations

- No real recording has been processed, so there are no accuracy or throughput
  numbers for anything.
- No YouTube video has actually been downloaded.
- OCR was measured on synthetic renders with clean text. Real frames carry
  compression artifacts and scaling that those fixtures do not.
- A reclaimed job restarts from the beginning; there is no mid-job resume, so a
  long recording that fails late repeats its work.
- Media parsing is isolated in a separate process, which is not a sandbox.
  `docs/media-sandbox.md` states exactly what that does and does not guarantee.
- `watch_runtime`, the least-privilege database role, is provisioned but unused:
  the runtime still authenticates as `service_role`.
- Screen text is read, not understood. There is no scene classification, object
  detection or visual embedding, by design.

## Documentation

| Document | Contents |
|---|---|
| `SECURITY.md` | Trust boundary and the controls that enforce it |
| `docs/runbook/workshop-analysis.md` | Analysing a recording, end to end |
| `docs/runbook/windows-claude-desktop.md` | Windows setup and Claude Desktop |
| `docs/adr/0002-screen-pipeline-and-mcp-extension.md` | Why the screen track exists and how the MCP surface grew |
| `docs/media-sandbox.md` | What media isolation guarantees, and what it does not |
| `docs/dependencies.md` | Lock file, audit, SBOM, media stack CVE position |
| `docs/threat-model/v1.md` | Threats and the status of each control |
| `docs/architecture/v1-baseline.md` | Frozen architecture |

Screen text, transcripts and metadata are untrusted data with
`instruction_authority = NONE`, enforced by database constraints. Text on a
screen saying "ignore all previous instructions" is content, exactly like a
spoken sentence.

Do not redefine frozen schemas, MCP permissions, trust boundaries or processing
modes without an explicit architecture decision.
