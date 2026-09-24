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

## Remote processing boundary

The remote processing API (ADR 0009) is the only network surface the Watch
exposes. It binds loopback and is reached over an SSH tunnel, so no public port is
opened, but the bearer token is required regardless: loopback is not
authentication, because anything else on the host can reach a loopback port.

The API refuses to start without a token of at least 32 characters. Starting
without one would accept every caller on the host. Tokens are compared with
`hmac.compare_digest`, and every malformed Authorization header is
indistinguishable from a wrong token.

The token is not the Supabase credential and grants none of its authority. A
client can submit media and read job state on that host. It cannot reach the
database, read a path, fetch a URL or run a command, because no route offers any
of those.

Authentication happens before the request body is read, so an unauthenticated
caller cannot spend the host's disk. An upload must declare its length, is
refused above the configured limit before any transfer, is capped again on the
bytes actually received, must match its declared digest, and is deleted if any of
that fails. Unverified bytes stage outside the content-addressed tree. Upload
filenames are attacker-controlled: they are cleaned, stored as metadata, and
never used to build a path.

Deduplication is decided on the digest the server computed, never on one a client
asserted.

Remote processing adds nothing to the MCP surface. Uploading and cancelling are
operator actions; exposing them to a model that had read a hostile transcript
would let it spend the host's disk or kill a running job.

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
  `watch_runtime` role exists but needs a token carrying its role claim. The
  remote deployment does not change this, but it does mean the credential now
  sits on an always-on VPS as well as on a laptop.
- Media isolation has no memory, CPU, filesystem or privilege limits.
- Worker/application logs are structured JSON events and systematically drop
  sensitive field classes such as secrets, tokens, cookies, URLs, filesystem
  paths, transcript text and prompt/content fields. The local operator-facing
  CLI may still print local paths for setup/diagnostics; MCP responses do not.
- Application-level resource limits now cover source bytes/duration, stream
  count, video dimensions, audio sample rate/channels, probe time/output,
  transcript segment/text volume, screen samples/observations and export size.
  OS-level confinement is now applied to the VPS deployment through the systemd
  units (`ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges`, a single
  `ReadWritePaths`, `MemoryMax`, `TasksMax`), but remains open for local runs.
- The remote API runs one worker. Two uploads queue rather than processing in
  parallel, and the host's free disk is the binding constraint on how much work
  can be accepted.

## Reporting

This is a private repository. Raise security concerns directly with the
maintainer rather than opening a public issue.
