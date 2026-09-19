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

YouTube:

```powershell
vorquel-watch ingest-url https://www.youtube.com/watch?v=VIDEO_ID
```

Both return an opaque `source_id`. That identifier is the only thing Claude
ever receives; no tool accepts a path or a URL.

A YouTube URL is reduced to a single canonical video, so `&list=` and `&index=`
parameters are discarded and a playlist cannot become hundreds of downloads.
Downloaded bytes pass the same Source Guard as a local file.

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

**What was actually shown:**

```
search_screen_text("Supabase", [source_id])
```

These answer different questions. Someone can mention a tool without showing
it, or show it without naming it. Comparing the two answers "which tools were
only talked about, and which were actually demonstrated".

**Both at once:**

```
get_context_at(source_id, timestamp_ms)
get_context_range(source_id, start_ms, end_ms)
```

This is the question the product exists for:

> "When he says this is the flow that receives the lead, the screen shows an n8n
> workflow containing Webhook, Normalize Lead, Supabase and Follow-up."

**Seeing the frame:**

```
get_frame(source_id, timestamp_ms)
```

Returns the image itself, so Claude looks at the screen rather than only reading
OCR text. The response reports the timestamp actually found, which may differ
slightly from the one requested.

## A worked example

1. `search_transcript("n8n", [src_...])` → a hit at 02:14:10
2. `get_context_at(src_..., 8050000)` → the sentence, plus the observation
   covering that instant and its OCR text
3. `get_frame(src_..., 8050000)` → the frame, if the OCR text is not enough

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
- No YouTube video has actually been downloaded.
- A job that dies is reclaimed and restarts from the beginning. There is no
  mid-job resume, so a five-hour recording that fails late repeats its work.
- OCR was measured on synthetic renders with clean text: full recall on
  dashboard UI, terminal, code and accented Portuguese. Real frames carry
  compression artifacts and scaling that those fixtures do not.
