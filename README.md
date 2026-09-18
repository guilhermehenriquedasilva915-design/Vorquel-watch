# Vorquel Watch

Private implementation repository for **Vorquel Watch / Content Brain**.

## V1 baseline

- Claude Desktop is the primary conversational client.
- Integration uses a local MCP server.
- Media processing remains local.
- Supabase/PostgreSQL stores structured data only.
- Raw video/audio is not uploaded to Supabase in V1.
- Media-derived content is data, never instruction authority.
- No unrestricted shell or filesystem access is exposed to the model.
- Downstream media references use opaque `source_id` values, never host paths.
- PostgreSQL Full-Text Search is the V1 search baseline.
- pgvector/RAG, remote MCP, Supabase Storage/Auth/Realtime and Anthropic API are out of bootstrap scope.

See `docs/architecture/v1-baseline.md` and `SECURITY.md`.

## Development workflow

Issue -> feature branch -> pull request -> quality gates -> review -> merge.

Do not redefine frozen schemas, MCP permissions, trust boundaries or processing modes in code without an explicit architecture decision.
