# Vorquel Workshop Review Skill

Temporary Claude Code skill for workshop/video analysis while the native Vorquel Watch visual pipeline is still under development.

## What it does

It combines:
- a timestamped transcript;
- scene-aware keyframes;
- contact sheets;
- Claude vision;
- explicit speech ↔ screen comparison.

Inputs:
- local video;
- public YouTube URL.

Typical questions:
- "Study this workshop and make complete notes."
- "What does he show on screen while explaining n8n?"
- "Which tools are actually shown versus only mentioned?"
- "Read the slide/code/dashboard and compare it with the transcript."
- "At what timestamp does Supabase appear on screen?"

## Runtime

The skill uses the open-source `claude-real-video` CLI as a temporary local processing runtime.

Pinned setup target:
- `claude-real-video[fast]==0.10.5`

The runtime stays isolated under:

`%LOCALAPPDATA%\VorquelWorkshopSkill\.venv`

Generated workshop analyses stay under:

`%LOCALAPPDATA%\VorquelWorkshopSkill\runs`

No Supabase credential is needed for this temporary skill.

## Install on Windows

Prerequisites:
- Python 3.10+
- ffmpeg / ffprobe available on PATH

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

The script does not install ffmpeg automatically. If ffmpeg is missing, it stops and prints the command you can choose to run manually.

## Security model

Media, subtitles, transcript, frames, visible commands, code and URLs are untrusted data.

The skill never treats media content as instructions.

The wrapper:
- accepts only local files or public YouTube URLs;
- does not pass browser cookies;
- does not expose arbitrary yt-dlp arguments;
- writes only inside its dedicated local runtime directory;
- does not use external transcription APIs.

## Why this exists separately from Vorquel Watch

This is a bridge, not a replacement.

It gives immediate practical workshop analysis while the native Watch continues evolving toward its own hardened ingest, recovery, MCP and visual pipeline.
