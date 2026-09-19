# Vorquel Watch

Private implementation repository for Vorquel Watch / Content Brain.

Local-first media analysis: a local file becomes a searchable, timestamped
transcript that Claude Desktop can query through a local MCP server, without
raw media ever leaving the machine.

## Current state

The V1 security and integrity baseline is complete. FAST transcription,
long-media resume, STANDARD, SPEAKERS and the local UI are not.

| Area | State |
|---|---|
| Source Guard | Container and codec allowlist, out-of-process parse, content hashing |
| Storage | Content-addressed, re-verified before reuse and before processing |
| Database | Single-source provenance, job state machine, atomic completion, leases |
| MCP | Frozen 14-tool surface, explicit field projections, identifier validation |
| Supply chain | Hash-pinned lock, audited and SBOM'd in CI, models pinned to a commit |
| Credential | Encrypted by the OS credential store, never in a file you can read |
| FAST transcription | Implemented, **not yet run end to end** |
| Long media resume | Job-level recovery only; chunking and checkpoints not done |
| STANDARD / SPEAKERS | Not implemented |
| Local UI | Not implemented |

### Data flow

```
local file -> Source Guard -> content-addressed local storage
                                        |
local worker -> Faster-Whisper -> transcript segments -> Supabase
                                        |
Claude Desktop -> local MCP -> structured data only
```

Raw video and audio stay on the machine. Supabase holds structured metadata,
transcripts and provenance, never media bytes and never host paths.

## Setup on Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\setup.ps1
.\.venv\Scripts\python.exe -m vorquel_watch.cli configure --url https://xnygwzuckijaxzlszfpn.supabase.co
```

`configure` asks for the Supabase secret key with hidden input and encrypts it
with DPAPI. It is never written to a file in readable form and never enters the
repository or the Claude configuration.

Then:

```powershell
.\.venv\Scripts\python.exe -m vorquel_watch.cli doctor
.\scripts\windows\start-worker.ps1
```

Merge the snippet from `%LOCALAPPDATA%\VorquelWatch\claude-mcp.json` into Claude
Desktop's MCP configuration and restart it. The snippet contains no secret; the
MCP process reads the credential from the OS store itself.

## Using it

```powershell
.\.venv\Scripts\python.exe -m vorquel_watch.cli ingest C:\path\to\video.mp4
```

The path is a local CLI argument only. It is never an MCP argument, and no MCP
tool accepts a filesystem path or a URL. Ingest returns an opaque `source_id`;
that is what you give Claude.

Then ask Claude to analyse that `source_id`. It should call
`get_capabilities` → `get_source` → `start_analysis` → `get_job` →
`search_transcript` → `get_segment`, and cite findings with timestamps.

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
carries the widest legacy codec surface for the least value here. See
`docs/media-sandbox.md`.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"
.\.venv\Scripts\python.exe scripts\security\check_repo.py
```

Dependencies are locked with hashes. See `docs/dependencies.md` before changing
`pyproject.toml`.

## Limitations

- FAST has not been run end to end against real media yet.
- A reclaimed job restarts from the beginning; there is no mid-job resume.
- No benchmark has been run, so there are no accuracy or throughput numbers.
- Media parsing is isolated in a separate process, which is not a sandbox.
  `docs/media-sandbox.md` states exactly what that does and does not guarantee.
- `watch_runtime`, the least-privilege database role, is provisioned but unused:
  the runtime still authenticates as `service_role`.

## Documentation

| Document | Contents |
|---|---|
| `SECURITY.md` | Trust boundary and the controls that enforce it |
| `docs/media-sandbox.md` | What the media isolation guarantees, and what it does not |
| `docs/dependencies.md` | Lock file, audit, SBOM, media stack CVE position |
| `docs/threat-model/v1.md` | Threats and the status of each control |
| `docs/architecture/v1-baseline.md` | Frozen architecture |
| `docs/runbook/windows-claude-desktop.md` | Operating runbook |

Do not redefine frozen schemas, MCP permissions, trust boundaries or processing
modes without an explicit architecture decision.
