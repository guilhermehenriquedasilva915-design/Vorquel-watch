# Supabase schema

Dedicated project: `vorquel-content-brain` (`xnygwzuckijaxzlszfpn`, `sa-east-1`).

The Git repository is the source of truth for schema migrations.

Migration order:
1. Apply every `*.sql` file in `supabase/migrations` in lexical order.
2. `0021` through `0023` are reserved by the parallel scoped-knowledge
   lineage and are intentionally absent from this branch.
3. `0024_knowledge_approval_integrity.sql` repairs legacy approval after
   `0020` made `knowledge_items.valid_from` mandatory. It explicitly writes
   `approved_at` and `valid_from` with the same timestamp; it does not weaken
   the constraint or add a table-wide default.

Migrations 0008 and 0009 are required, not optional: 0008 makes cross-source
provenance impossible and 0009 creates `complete_processing_job`, which the
worker calls to finish every job. Skipping them leaves job completion broken.

The `knowledge-migrations` quality job provisions a disposable PostgreSQL 17
database, bootstraps the Supabase-managed roles, applies the complete migration
chain from zero, and executes `tests/knowledge_approval_integrity.sql`. The SQL
acceptance covers proposal, linked provenance, human approval, ACTIVE search
and synthesis, copied provenance, supersession, withdrawal, lifecycle history,
and the `UNTRUSTED_DERIVED` / `NONE` trust boundary.

Do not place Supabase server credentials in this directory or in browser code.
