# Supabase schema

Dedicated project: `vorquel-content-brain` (`xnygwzuckijaxzlszfpn`, `sa-east-1`).

The Git repository is the source of truth for schema migrations.

Migrations are applied in filename order, `0001` through `0023`. The CI job
`knowledge-migrations` applies all of them to a disposable PostgreSQL on every
pull request, so the order in this directory is the order that is tested.

Migrations 0008 and 0009 are required, not optional: 0008 makes cross-source
provenance impossible and 0009 creates `complete_processing_job`, which the
worker calls to finish every job. Skipping them leaves job completion broken.

**0021, 0022 and 0023 must be applied together, in that order.** 0021 adds the
scope columns and the generic provenance locator, but the approval path only
learns to carry that locator in 0023. A project running 0021 without 0023 drops
`locator_kind`/`locator` at the moment a candidate becomes knowledge, losing the
provenance of every non-media source.

Do not place Supabase server credentials in this directory or in browser code.

## Testing a migration before it touches the managed project

Nothing here is applied live before it is green in CI against a throwaway
database. To reproduce that locally:

```bash
docker run --rm -d --name vorquel-pg-test \
  -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:17

export PGHOST=localhost PGPORT=5432 PGDATABASE=postgres \
       PGUSER=postgres PGPASSWORD=postgres

# Supabase provisions anon/authenticated/service_role/authenticator for us;
# a bare PostgreSQL does not, so the migration set cannot apply without them.
psql -v ON_ERROR_STOP=1 -f supabase/tests/bootstrap.sql

for migration in supabase/migrations/[0-9]*.sql; do
  psql -v ON_ERROR_STOP=1 -f "$migration"
done

psql -v ON_ERROR_STOP=1 -f supabase/tests/knowledge_scope_and_external_sources.sql
```

`bootstrap.sql` exists only to make a disposable database a faithful stand-in
for the managed project. **It is never applied live.**

`knowledge_scope_and_external_sources.sql` asserts the invariants that decide
whether the knowledge store can be trusted: one client never retrieves
another's knowledge, a pending candidate answers nothing until a human approves
it, `SECRET` is not a representable classification, a locator is an address
rather than a payload, and re-learning identical bytes changes nothing.
