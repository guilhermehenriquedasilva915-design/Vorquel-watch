# Remote Processing V1 — E2E evidence and long-workshop plan

Status: short-file E2E PASS; long-workshop E2E and real-VPS E2E pending
Recorded: 2026-09-23
Branch: `feat/remote-processing-v1`

## Scope of what was actually executed

A real ASGI server (uvicorn) on real loopback sockets, a real streaming upload
over HTTP, real bearer authentication, the real client, a real MP4 that went
through the real out-of-process media probe, and the real content-addressed
store.

One boundary was stubbed and it is named here rather than implied: `create_source`
and `find_source_by_hash` were held in memory, so this run wrote **no rows into
the live Supabase project**. Everything the remote feature itself owns was
exercised for real.

## MEDIDO — short-file E2E

Media: 4,611-byte H.264/AAC MP4, 30 frames, generated for the run.

| Step | Check | Result |
|---|---|---|
| health | unauthenticated route returns a constant only | PASS |
| auth | wrong token rejected; token absent from the error | PASS |
| upload | HTTP 202, `source_id`, `job_id` returned | PASS |
| upload | job comes back `QUEUED`, not processed inline | PASS |
| upload | duration probed from real media (> 0 ms) | PASS |
| upload | detected as `LOCAL_VIDEO` | PASS |
| upload | server digest equals the locally computed sha256 | PASS |
| upload | no host path anywhere in the response | PASS |
| storage | object filed under its digest | PASS |
| storage | `verify_object` re-hash succeeds | PASS |
| storage | no `.part` file left in `incoming/` | PASS |
| status | `GET /v1/jobs/{id}` returns the job | PASS |
| list | job present; `status` filter works | PASS |
| dedupe | re-upload transfers **no bytes** (`skipped_upload`) | PASS |
| dedupe | forced re-upload still reuses source and job server-side | PASS |
| reject | non-media payload refused 415, no source created | PASS |
| reject | unknown job 404; malformed job id 400 | PASS |
| cancel | job moves to `CANCELLED` | PASS |
| detach | a brand-new client connection observes the same state | PASS |

27 of 27 checks green.

## MEDIDO — unit coverage of the limits

Covered by `backend/tests/test_remote_api.py`, because these are the paths a real
upload must never be able to take:

- missing `Content-Length` → 411, nothing read;
- declared size over the limit → 413 **before any transfer**;
- a **lying** `Content-Length` (small declaration, huge body) → cut off mid-stream
  at 413, and the partial file deleted;
- a truncated body → 400 `LENGTH_MISMATCH`, never ingested, because accepting it
  would yield a transcript that silently stops early;
- declared digest mismatch → 400, nothing ingested, no job created;
- insufficient free space → 507;
- unauthenticated upload → 401 and **nothing written to disk**;
- a traversal filename stays metadata; the staged file is a uuid `.part`;
- an ingest crash returns 500 carrying no path or internal text.

## Not yet executed

Stated plainly rather than omitted:

1. **Real Supabase writes over the remote path.** The E2E stubbed persistence. The
   underlying calls are the same ones local ingest already uses in production, but
   the remote path has not written a row to the live project.
2. **Real VPS deployment.** This session had no SSH access and invented no
   credentials. The systemd units and the runbook are untested on the actual
   Oracle instance.
3. **Long-workshop E2E.** Nothing multi-gigabyte or multi-hour has gone through the
   remote path.

## Long-workshop test plan

Run once the VPS is deployed. The point is not to prove it works on a small file
again; it is to find where it stops working.

Source: the already-processed 1,629,994 ms real workshop video used for the
Visual Context acceptance, so results are comparable to a known baseline.

| # | Test | What it would catch | Expected |
|---|---|---|---|
| 1 | Upload the full recording over the tunnel | streaming regressions, timeouts | 202 with identifiers; steady memory on both ends |
| 2 | Record peak RSS of the API during upload | a reintroduced whole-file read | bounded, roughly one chunk, not file-sized |
| 3 | Close the laptop immediately after 202 | whether detaching actually works | job completes without the notebook |
| 4 | `remote status` from a new tunnel hours later | state durability | progress advances through the stages |
| 5 | `sudo reboot` mid-transcription | lease reclaim + chunk resume | job resumes near its last checkpoint, not from zero |
| 6 | `systemctl restart vorquel-watch-worker` mid-job | lease handover | another worker claims it; no duplicate transcript |
| 7 | Re-upload the same file | dedupe at scale | zero bytes transferred |
| 8 | Two uploads back to back | queueing under one worker | both queue; second waits, neither fails |
| 9 | Fill the disk to under 2 GB free, then upload | the free-space guard | 507 before any transfer |
| 10 | Compare the remote transcript to the local baseline | pipeline equivalence | same pipeline version, comparable output |

Record wall times for 1 and 5. The Visual Context acceptance already found that a
two-minute scene-aware range cost 135,317 ms, so throughput on this hardware is a
known open question and should be measured here rather than assumed.

## Verdict

The transport, its limits and its trust boundaries are proven at short-file scale
over a real server. Deployment and long-workshop behaviour are **unproven** and
are the remaining gates before this is trusted with a real workshop.
