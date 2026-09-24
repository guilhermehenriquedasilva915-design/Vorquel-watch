# ADR 0009: Remote Processing V1

Status: Accepted
Date: 2026-09-23
Supersedes: nothing. Extends the V1 Alpha control plane with a second ingest
transport.

## Context

A workshop recording takes hours to transcribe. The notebook that holds the file
is the machine least able to stay awake for that: it gets closed, carried around
and suspended. The Oracle VPS is already paid for and already awake.

The goal is narrow and worth stating precisely, because it bounds everything
else: upload once, get identifiers, disconnect, ask later.

The audit that preceded this decision found that most of the machinery already
existed. The V1 Alpha worker is not a script that runs to completion inside a
request — it is already a persistent poll loop that claims jobs under a lease,
heartbeats them, and reclaims work whose worker died:

- `processing_jobs` holds durable job state (migration 0011);
- `claim_next_job` picks or reclaims a job with `FOR UPDATE SKIP LOCKED`;
- `processing_runs.checkpoint` lets transcription resume at a chunk boundary
  (migration 0013);
- `LocalStorage` already stores content-addressed objects, re-hashes them before
  reuse and before processing, and quarantines a mismatch;
- `validate_and_hash` already applies the container allowlist, codec allowlist and
  every resource limit, parsing out of process;
- `find_source_by_hash` already deduplicates by checksum and
  `create_or_reuse_job` is already idempotent.

What was missing was not a pipeline. It was a way to get bytes onto the VPS and a
way to ask about them afterwards.

## Decision

Add a transport, not a second pipeline.

### 1. A small HTTP API in front of the existing service

`remote_api.py` is a Starlette application over `WatchService`:

| Route | Purpose |
|---|---|
| `GET /v1/health` | liveness; the only unauthenticated route; returns a constant |
| `POST /v1/uploads` | stream media in, register a source, queue a job |
| `GET /v1/sources/by-digest/{sha256}` | is this content already held? |
| `GET /v1/jobs` | recent jobs |
| `GET /v1/jobs/{job_id}` | one job |
| `POST /v1/jobs/{job_id}/cancel` | cancel queued or running work |

The service envelopes are passed through untouched. They already carry the
security block and already contain no host path, so rewriting them here could
only make them less accurate.

### 2. Loopback plus an SSH tunnel, not a public port

The API binds `127.0.0.1:8787`. The notebook reaches it with `ssh -L`.

This was chosen over a public port with TLS because it removes an entire class of
problem rather than defending against it: no new firewall rule, no DNS record, no
certificate to renew, no internet-facing listener. The alternative remains
available but is a separate decision with its own review, not a configuration
flag someone flips.

A bearer token is required anyway. Loopback is not authentication: anything else
on the host, including a compromised unrelated process, can reach a loopback port.

### 3. Bearer token, and the server refuses to start without one

`remote_auth.load_server_token()` reads the token from the environment or from a
file named by the environment, requires at least 32 characters, and raises
otherwise. An API that started without a credential would accept every caller on
the host, which is strictly worse than failing to boot.

Comparison uses `hmac.compare_digest`, so response timing does not leak the token
one character at a time. Every malformed header shape — wrong scheme, missing
credential, no header — produces the same outcome as a wrong token.

### 4. The token is not the Supabase credential

The notebook holds exactly one secret: this API token. It authorizes "submit
media on this host and read job state", and nothing more. A stolen notebook
cannot reach the database, because no route offers database access and the client
never receives the service key.

### 5. Remote Processing adds nothing to the MCP surface

Uploading, cancelling and listing are operator actions. Exposing them as MCP
tools would let a model that misread a hostile transcript spend the VPS's disk or
kill a running job. The MCP surface stays at exactly 24 tools, and a test asserts
that no remote tool appears on it.

### 6. Native systemd, not Docker

Two units with `Restart=always` and `WantedBy=multi-user.target`. Chosen over
containers because the runtime is already a native venv with pinned Whisper and
OCR model caches, and containerising it would complicate the model cache and the
content-addressed volume for no isolation the unit hardening does not already
provide: `ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges`,
`RestrictAddressFamilies`, a single `ReadWritePaths`, and `MemoryMax`/`TasksMax`
ceilings.

Those ceilings partly close the "OS-level CPU, memory, disk and process
concurrency confinement remains open" gap that `SECURITY.md` records — for these
two units. The gap remains open for local Windows runs.

### 7. No new migration

Remote adds no state that the schema does not already model. Where a source came
from is recorded as `ingest_origin: REMOTE_UPLOAD` inside the existing
`external_metadata` jsonb. Uploads and local ingests share one sources table, one
jobs table and one worker.

### 8. No new resolved dependency

`starlette`, `uvicorn` and `httpx` were already in `requirements.lock` as
transitive requirements of `mcp==2.2.0`. They are now declared in a `remote`
extra, pinned to the versions already locked, so the resolution does not move.
The API and the client are built on them rather than on a new framework.

## Upload handling

The order matters, and it is enforced in this order:

1. `Content-Length` is required — without it the size limit could not be applied
   before reading the body. Missing it is a 411.
2. A declaration over `max_source_bytes` is refused before a single byte is
   transferred (413).
3. Free space is checked, keeping a 2 GB margin so a completed upload cannot fill
   the disk and strand the worker, which needs scratch space (507).
4. The body streams to `data_dir/incoming/<uuid>.part`, hashed as it arrives.
   Nothing is buffered whole: a multi-gigabyte upload costs one chunk of RAM. The
   cap is enforced again on bytes actually received, because `Content-Length` is
   a claim.
5. Received length must match the declaration, and the computed digest must match
   the declared one if the client sent it. A truncated transfer is a failed
   transfer, not media to validate — otherwise a partial recording becomes a
   source whose transcript silently stops early.
6. Only then does `ingest_uploaded_file` run the same probe, the same allowlists
   and the same content-addressed store a local ingest uses.
7. The partial file is deleted in a `finally`, whatever happened.

The unverified staging directory is deliberately outside the content-addressed
tree: a file that has not been validated must never sit where a digest-addressed
object is expected.

`purge_stale_uploads` runs at startup. A process killed mid-upload leaves a
`.part` file no request will ever finish, and nothing references it.

## Deduplication

Two layers, and only one of them is trusted.

`GET /v1/sources/by-digest/{sha256}` lets the client skip re-sending gigabytes it
already sent. That answer is **advisory**. The decision that actually creates or
reuses a source is made on the digest the *server* computed from the bytes it
received, never on one a client asserted. A client cannot bind a job to content
it did not upload.

## Untrusted data is still untrusted

Arriving over the network buys a source nothing. Media, transcripts and OCR keep
`data_trust_class = UNTRUSTED_MEDIA` / `UNTRUSTED_DERIVED` and
`instruction_authority = NONE`. The upload filename is attacker-controlled: it is
cleaned, recorded as metadata, and never used to build a path, because the object
is filed under its digest.

## Consequences

Good:

- the notebook becomes disposable, which was the entire point;
- reboot recovery needed no new code — it is the existing lease plus
  `Restart=always`;
- re-uploading a recording costs no transfer;
- local mode is untouched and still the default.

Accepted costs:

- the tunnel is a manual step before each session;
- the VPS's disk is now the binding constraint, and a full disk is the most
  likely operational failure;
- the runtime still authenticates to Supabase as `service_role`. That pre-existing
  gap is now reachable from one more place, so it matters more than it did, and it
  is restated here rather than quietly inherited;
- concurrency is one worker. Two uploads queue; they do not process in parallel.

## What this does not do

No RAG, no embeddings, no vector store, no JEV, no agent orchestration. No media
in Supabase. No change to the Brain, to Analyze, to the knowledge lifecycle or to
the UI. No public port.
