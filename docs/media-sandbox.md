# Media parsing isolation (SEC-02)

## The problem

Parsing media means handing attacker-controlled bytes to FFmpeg's demuxers and
decoders. That stack has a long history of memory-safety bugs — the most recent
relevant one being CVE-2026-8461 ("PixelSmash"), a heap out-of-bounds write in
the MagicYUV decoder reachable through the AVI, MKV and MOV demuxers.

Before this change, `probe_media` called PyAV directly, so that parse happened
inside the worker and the MCP server — the same processes that hold the
Supabase credential and the database connection.

## What actually happens now

`vorquel_watch.media_probe` runs as a separate process and is the only place
where hostile bytes meet FFmpeg. It reports raw observations and makes no
decisions. The container allowlist, codec allowlist, stream cap and duration
limits are all applied by the parent, on plain JSON. A compromised parse
therefore cannot approve itself.

## What this guarantees

Each of these is enforced in `source_guard._run_probe` and was verified on
Windows 11:

| Control | How |
|---|---|
| Separate process | `subprocess.run` with explicit argv |
| No shell | `shell=False`; no string is ever passed to a command interpreter |
| No inherited environment | Only 7 variables are forwarded; the Supabase credential is not among them |
| Wall-clock timeout | `VORQUEL_WATCH_PROBE_TIMEOUT`, default 60s |
| Bounded output | stdout over 64 KiB is rejected unread |
| No stdin | `stdin=subprocess.DEVNULL` |
| Controlled working directory | the media file's own directory |
| Sanitized failures | only fixed error codes cross the boundary; stderr is discarded because it carries FFmpeg text and the input path |

Measured: a spawned child sees exactly `PATH`, `PYTHONDONTWRITEBYTECODE`,
`PYTHONNOUSERSITE`, `SYSTEMROOT`, `TEMP`, `TMP`, `WINDIR`. A canary secret
placed in the parent environment is absent from the child process.

## What this does NOT guarantee

This is process isolation, not a sandbox. Saying otherwise would be false.

- **No memory limit.** A decoder that allocates without bound can still exhaust
  system memory. Windows Job Objects could cap this; they are not wired up.
- **No CPU or I/O limit.** Only wall-clock time is bounded.
- **No filesystem confinement.** The child runs with the same user token and can
  read and write anything that user can. It is not chrooted, jailed, or run in
  an AppContainer or a restricted token.
- **No privilege reduction.** Same user, same integrity level as the parent.
- **No syscall filtering.** There is no seccomp equivalent in use.
- **No network restriction.** The child could open sockets. It has no reason to,
  but nothing stops it.

So the realistic claim is: **a crash, hang, or memory blow-up in FFmpeg takes
down a short-lived child instead of the worker, and code execution in that child
starts without the process's secrets.** An attacker who achieves code execution
still has the user's privileges.

Closing the remaining gaps on Windows means a Job Object with memory and CPU
caps, and ideally a restricted token. That is tracked work, not done here.

## Cost

Measured on this machine (Ryzen 7 5700U): **~373 ms per probe**, against a few
milliseconds in-process. This is paid once per ingest, not per chunk or per
segment, so it does not affect transcription throughput. The test suite went
from 2.0s to 6.6s for the same reason.

That trade is worth it: ingest happens once per file, and the alternative is
parsing hostile input in the process that holds the database credential.

## Restricting the surface is still the first defence

Isolation does not replace the allowlist. The bundled FFmpeg build enables 557
codecs; Source Guard accepts six containers and a short list of codecs, and
excludes AVI outright. See `backend/src/vorquel_watch/source_guard.py`.
