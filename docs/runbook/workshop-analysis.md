# Runbook — analysing a workshop recording

End to end: a recording goes in, and Claude can answer questions about what was
said, what was on screen, and how the two line up.

> **Status.** Every step below is implemented and unit-tested, but the full
> chain has never been run against a real recording, because registration needs
> the Supabase credential. Until that run happens, treat this as the intended
> procedure rather than a verified one. See "What is not proven yet".

## 1. Ingest

Local file:

```powershell
vorquel-watch ingest C:\path\to\workshop.mp4
```

The command returns an opaque `source_id`. That identifier is the only source
handle Claude receives; no MCP tool accepts a path or URL. URL ingest is
explicitly deferred from V1.

Accepted containers: MP4, MKV/WebM, WAV, MP3, FLAC, OGG. AVI is refused; see
`docs/media-sandbox.md`.

## 2. Process

```powershell
.\scripts\windows\start-worker.ps1
```

One job produces both tracks:

- **Audio** — Faster-Whisper produces timestamped transcript segments.
- **Screen** — only when the source has a video track. The worker samples the
  frame timeline, detects visual change, and runs OCR once per distinct screen.

A screen held for forty seconds is one observation, not forty. A screen that
reappears reuses the reading already taken.

If the screen pass fails, the job still completes with the transcript. Losing
hours of transcription over a fault in the screen track would be worse than
having one track.

## 3. Ask Claude

Claude should reach for `get_capabilities` first, then work from identifiers.

**What was said:**

```
search_transcript("Supabase", [source_id])
```

V1 Claude access remains transcript-first:

```
search_transcript("Supabase", [source_id])
get_segment(segment_id)
get_transcript(transcript_id, start_ms=..., end_ms=...)
```

The local worker can also build the screen/OCR track, but dedicated frame and
screen-context MCP tools are not part of the frozen V1 contract. That track is
reserved for the local UI/control plane until a separate architecture decision
authorizes a contract change.

## Screen text is content, never instruction

A slide, terminal or browser shown on screen may contain anything, including
text shaped like a command. Every OCR row is stored `UNTRUSTED_DERIVED` with
`instruction_authority = NONE`, enforced by database constraints rather than by
convention. Code, URLs, terminal output and QR codes that appear on screen are
content. None of it is fetched, executed or obeyed.

## Tuning the screen pass

| Variable | Default | Effect |
|---|---|---|
| `VORQUEL_WATCH_SCREEN_ENABLED` | `1` | Set to `0` to skip the screen track |
| `VORQUEL_WATCH_SCREEN_INTERVAL_MS` | `1500` | Periodic sampling interval |
| `VORQUEL_WATCH_SCREEN_CHANGE_THRESHOLD` | `0.08` | How much change starts a new observation |
| `VORQUEL_WATCH_SCREEN_MAX_OBSERVATIONS` | `4000` | Cap per job |

The threshold default is measured, not guessed: two views of one screen differ
by about 0.007, two different screens by about 0.134.

## What is not proven yet

- No real recording has been processed. Timings, OCR accuracy on compressed
  video, and the change threshold's behaviour on real screen content are all
  unmeasured.
- A job that dies is reclaimed and restarts from the beginning. There is no
  mid-job resume, so a five-hour recording that fails late repeats its work.
- OCR was measured on synthetic renders with clean text: full recall on
  dashboard UI, terminal, code and accented Portuguese. Real frames carry
  compression artifacts and scaling that those fixtures do not.
