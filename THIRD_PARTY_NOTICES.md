# Third-Party Notices

No third-party source code is vendored in the Vorquel Watch core.

## Temporary workshop-review skill

The temporary `skills/claude/workshop-review` skill orchestrates, but does not vendor, the external `claude-real-video` runtime:

- Project: `HUANGCHIHHUNGLeo/claude-real-video`
- Runtime target used by the setup script: `claude-real-video[fast]==0.10.5`
- License: MIT
- Purpose: local scene-aware keyframe extraction, timestamped transcript, frame timestamp mapping, contact sheets, local YouTube/local-file preprocessing.

The skill's cross-modal notes workflow was also informed by the public MIT-licensed `devinilabs/claude-watch` project, especially its pattern of pairing scene frames with timestamped transcript sections. No source code from that repository is vendored here.

The Agent Skills folder/frontmatter structure follows the public Anthropic Agent Skills format.

If source code is later copied or ported from any third-party project, its exact license and attribution requirements must be reviewed and recorded before merge.
