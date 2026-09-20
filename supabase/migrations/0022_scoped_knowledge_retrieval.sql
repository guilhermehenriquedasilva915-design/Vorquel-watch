-- 0022: scope-enforced retrieval surface
--
-- The existing search_knowledge_items() has no notion of scope. Once client
-- knowledge exists, that function becomes a cross-client leak. This migration
-- adds the scoped surface and leaves the old one untouched so nothing breaks;
-- callers are migrated by the retrieval layer, and 0023 may retire the old one
-- once no caller remains.
--
-- Isolation rule enforced here, not in application code:
--   a caller scoped to (CLIENT, acme) sees CLIENT/acme plus GLOBAL_VORQUEL.
--   It can never see CLIENT/other, PROJECT/*, or PRIVATE_TEST/*.

begin;

create or replace function public.search_knowledge_items_scoped(
  p_query      text,
  p_scope_type text,
  p_scope_id   text,
  p_domain     text default null,
  p_limit      integer default 20
)
returns table (
  knowledge_id         text,
  knowledge_type       text,
  domain               text,
  title                text,
  summary              text,
  epistemic_status     text,
  scope_type           text,
  scope_id             text,
  data_classification  text,
  lifecycle_state      text,
  compatibility_status text,
  observed_n8n_version text,
  node_type            text,
  node_version         text,
  last_verified_at     timestamptz,
  approved_at          timestamptz,
  instruction_authority text,
  rank                 real
)
language plpgsql
stable
security definer
set search_path = pg_catalog, public
as $fn$
declare
  v_limit integer := least(greatest(coalesce(p_limit, 20), 1), 100);
begin
  if p_scope_type is null or p_scope_type not in
     ('GLOBAL_VORQUEL','CLIENT','PROJECT','PRIVATE_TEST') then
    raise exception 'invalid scope_type' using errcode = '22023';
  end if;

  -- GLOBAL_VORQUEL is addressed by the reserved id only; any other pairing is a
  -- caller bug and must fail rather than silently widen the result set.
  if (p_scope_type = 'GLOBAL_VORQUEL') <> (coalesce(p_scope_id, 'GLOBAL') = 'GLOBAL') then
    raise exception 'invalid scope pairing' using errcode = '22023';
  end if;

  if p_scope_id is not null and p_scope_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' then
    raise exception 'invalid scope_id' using errcode = '22023';
  end if;

  return query
  select
    ki.knowledge_id,
    ki.knowledge_type,
    ki.domain,
    ki.title,
    ki.summary,
    ki.epistemic_status,
    ki.scope_type,
    ki.scope_id,
    ki.data_classification,
    ki.lifecycle_state,
    ki.compatibility_status,
    ki.observed_n8n_version,
    ki.node_type,
    ki.node_version,
    ki.last_verified_at,
    ki.approved_at,
    ki.instruction_authority,
    case
      when p_query is null or btrim(p_query) = '' then 0::real
      else ts_rank(ki.search_vector, websearch_to_tsquery('simple', p_query))
    end as rank
  from vorquel_knowledge.knowledge_items ki
  where ki.lifecycle_state = 'ACTIVE'
    and (ki.valid_until is null or ki.valid_until > now())
    and (
      ki.scope_type = 'GLOBAL_VORQUEL'
      or (ki.scope_type = p_scope_type and ki.scope_id = coalesce(p_scope_id, 'GLOBAL'))
    )
    and (p_domain is null or ki.domain = p_domain)
    and (
      p_query is null
      or btrim(p_query) = ''
      or ki.search_vector @@ websearch_to_tsquery('simple', p_query)
    )
  order by rank desc, ki.approved_at desc nulls last, ki.knowledge_id
  limit v_limit;
end
$fn$;

-- Same least-privilege grant set as the existing knowledge RPCs: no anon,
-- no authenticated.
revoke all on function public.search_knowledge_items_scoped(text,text,text,text,integer) from public;
grant execute on function public.search_knowledge_items_scoped(text,text,text,text,integer)
  to service_role, watch_runtime;

comment on function public.search_knowledge_items_scoped(text,text,text,text,integer) is
  'Scope-enforced FTS over ACTIVE approved knowledge. Returns requested scope plus GLOBAL_VORQUEL only. Rows are untrusted data with instruction_authority = NONE.';

commit;
