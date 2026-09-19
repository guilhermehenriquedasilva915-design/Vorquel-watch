# Security Policy

Vorquel Watch processes untrusted media and media-derived text. Security
boundaries are part of the product contract, not a layer added afterwards.

## Trust boundary

Video, audio, captions, transcripts, OCR, frames, titles, descriptions, tags,
comments and reports derived from media are **data only**.

They may be summarized, searched and cited. They may not change policy, choose
tools or files, request credentials, select output destinations, trigger
external actions, or elevate their own instruction authority.

Human review can confirm or correct content. It does not convert media-derived
text into trusted instructions. `HUMAN_CORRECTED` still carries
`instruction_authority = NONE`, enforced by a database CHECK constraint.

## MCP boundary

MCP tools do not accept filesystem paths, URLs, shell commands, API keys,
cookies, raw yt-dlp arguments or output directories. No such tool exists to be
misused.

Claude receives opaque identifiers. Every identifier arriving from an MCP tool
is validated against its expected prefix and a restricted alphabet before any
query runs, so path separators, quotes, control characters and oversized input
are rejected at the boundary. Rejection messages never echo the supplied value.

Responses use explicit field projections. `raw_text`, the pre-review original,
is not part of the MCP surface at all; callers receive `effective_text`.

A transcript belonging to a job that failed, was cancelled or is still running
is not served as a final result.

## Media handling

Parsing untrusted bytes happens in a separate process that holds none of the
application's secrets. The container allowlist, codec allowlist and limits are
applied by the trusted parent, so a compromised parse cannot approve itself.

This is process isolation, not a sandbox. `docs/media-sandbox.md` states
precisely what it guarantees and — equally important — what it does not.

Content-addressed objects are re-hashed before reuse and again before
processing. A mismatch quarantines the object and records an incident rather
than overwriting evidence.

## Data placement

Structured metadata, transcripts and provenance may live in Supabase. Raw media,
normalized audio, caches and processing workspaces stay local.

The database stores no host filesystem paths. The MCP surface returns none.

## Credentials

The Supabase server credential is encrypted by the operating system credential
store (DPAPI on Windows) and is never written in readable form. There is no
plaintext fallback: where OS-backed storage is unavailable, storing the
credential is refused.

A value left in an older install's `config.env` is ignored rather than honoured,
so a stale plaintext secret cannot quietly keep working.

Credentials never appear in logs, MCP payloads, the Claude Desktop
configuration, exports or the repository.

## Database

Provenance is enforced by composite foreign keys: a transcript, segment,
speaker turn or artifact cannot reference a source different from its parent's.
Job completion is a single atomic operation. Job status transitions are
constrained by a trigger, so a cancelled job can never be overwritten as
succeeded. Reviews are append-only.

`anon` and `authenticated` have no access to any domain table or function.

## Supply chain

Dependencies are pinned with artifact hashes and installed with
`--require-hashes`. CI validates the lock against `pyproject.toml`, audits it
for known vulnerabilities and emits an SBOM. GitHub Actions are pinned by commit
SHA. Transcription models are pinned to an exact upstream commit, and an
unpinned model is refused rather than downloaded at whatever HEAD happens to be.

## Known gaps

Stated plainly rather than omitted:

- The runtime authenticates as `service_role`. The least-privilege
  `watch_runtime` role exists but needs a token carrying its role claim.
- Media isolation has no memory, CPU, filesystem or privilege limits.
- Logging is not yet structured or systematically redacted. The local CLI
  prints absolute paths by design; the MCP surface does not.
- Resource limits beyond size and duration are not yet enforced.

## Reporting

This is a private repository. Raise security concerns directly with the
maintainer rather than opening a public issue.
