\set ON_ERROR_STOP on

-- Supabase provisions these roles for us. A disposable PostgreSQL does not, so
-- the migration set cannot be applied without them. This file exists only to
-- make CI a faithful stand-in for the managed project; it is never applied live.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
  -- 0010 grants watch_runtime to authenticator, so the grantee must exist
  -- before that migration runs.
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create role authenticator nologin noinherit;
  end if;
end
$$;
