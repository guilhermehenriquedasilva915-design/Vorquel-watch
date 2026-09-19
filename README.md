# Vorquel Watch

Private implementation repository for Vorquel Watch / Content Brain.

Give it a local workshop recording and it transcribes what was said while the
local worker can also build a timestamped screen/OCR track. The V1 Claude MCP
surface remains the frozen 14-tool contract; dedicated screen/frame MCP tools
and URL ingest are deferred until a separate architecture decision.

> "When he says this is the flow that receives the lead, the screen shows an n8n
> workflow containing Webhook, Normalize Lead, Supabase and Follow-up."

## Current state

| Area | State |
|---|---|
| Local file ingest | Implemented |
| Transcription (FAST) | Implemented, **never run end to end** |
| Screen tracking, change detection, dedupe | Implemented, measured on synthetic fixtures |
| OCR | Implemented, measured on synthetic fixtures |
| Frame retrieval by timestamp | Implemented and measured |
| Combined speech + screen context | Implemented |
| MCP surface | 14 frozen tools, contract-tested |
| Security and database integrity | Complete, verified against the live project |
| Long-video resume | Job-level recovery only; no mid-job resume |
| STANDARD / SPEAKERS / local UI | Not implemented |

Nothing has been run against a real recording yet: registration needs the
Supabase credential. Every claim above marked "measured" was measured on
synthetic fixtures, which is not the same thing.

### Data flow

```
local file ─────> Source Guard ─> content-addressed local storage
                                           │
                                           ├─> Faster-Whisper ─> transcript segments
                                           └─> sample / change detect / OCR ─> screen observations
                                                           │
                           Claude Desktop ─> frozen local MCP ─> bounded transcript/provenance data
```

Raw media stays on the machine. Supabase holds structured metadata,
transcripts, screen observations and provenance — never media bytes, never host
paths.

## Setup on Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\setup.ps1
vorquel-watch configure --url https://klirypiamkkwwdhvesar.supabase.co
vorquel-watch doctor
.\scripts\windows\start-worker.ps1
```

`configure` takes the Supabase secret with hidden input and encrypts it with
DPAPI. It never lands in a readable file, the repository, or the Claude
configuration.

Merge the snippet from `%LOCALAPPDATA%\VorquelWatch\claude-mcp.json` into Claude
Desktop and restart it. The snippet carries no secret; the MCP process reads the
credential from the OS store itself.

The Windows V1 setup installs the reviewed hash-locked runtime including
screen/OCR. URL/network ingest is not installed or exposed in V1.

## Using it

```powershell
vorquel-watch ingest C:\path\to\workshop.mp4
```

The command returns an opaque `source_id`. The filesystem path is accepted
only by the local control plane; no MCP tool accepts a path or URL.

Then ask Claude about that `source_id`. The questions it can answer:

| Question | Tools |
|---|---|
| Question | Frozen V1 tools |
|---|---|
| What was said about X? | `search_transcript` |
| Open the relevant spoken context | `get_segment` / `get_transcript` |
| Inspect source/job/provenance | `get_source` / `get_job` |

The worker may persist screen/OCR observations, but V1 does not expose new
screen-specific MCP tools. Screen review belongs to the local control plane/UI
until an explicit architecture decision changes the frozen MCP contract.

See `docs/runbook/workshop-analysis.md` for the current procedure.

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
| `docs/adr/0002-screen-pipeline-and-mcp-extension.md` | Historical screen/MCP proposal; partly superseded |
| `docs/adr/0003-restore-frozen-mcp-and-defer-url-ingest.md` | Restores the frozen V1 boundary |
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
