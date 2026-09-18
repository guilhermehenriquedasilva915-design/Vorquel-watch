# ADR 0001 — Supabase for structured data, local storage for media

Status: Accepted
Date: 2026-09-18

## Decision

Use Supabase/PostgreSQL as the V1 structured-data store while keeping raw media and heavy processing local.

## Consequences

- schema changes are migrations committed to Git;
- service credentials stay in the backend/control plane;
- anonymous/authenticated direct table access is not required in V1;
- RLS remains enabled as defense in depth;
- raw media paths are never part of public/MCP contracts;
- FTS is implemented in PostgreSQL;
- semantic/vector search is deferred until justified.
