-- 0024: restrict scoped knowledge RPC execution
--
-- Supabase grants EXECUTE on new public functions to anon/authenticated by
-- default. 0022/0023 revoked PUBLIC but did not remove those explicit role
-- grants. These RPCs are SECURITY DEFINER and must remain backend-only.
--
-- Preserve execution for service_role and watch_runtime only.

begin;

revoke all on function public.search_knowledge_items_scoped(text,text,text,text,integer)
  from public, anon, authenticated;
revoke all on function public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)
  from public, anon, authenticated;
revoke all on function public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)
  from public, anon, authenticated;
revoke all on function public.approve_knowledge_candidates_batch(text[],text,text)
  from public, anon, authenticated;

grant execute on function public.search_knowledge_items_scoped(text,text,text,text,integer)
  to service_role;
grant execute on function public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)
  to service_role;
grant execute on function public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)
  to service_role;
grant execute on function public.approve_knowledge_candidates_batch(text[],text,text)
  to service_role;

do $grant$
begin
  if exists (select 1 from pg_roles where rolname = 'watch_runtime') then
    grant execute on function public.search_knowledge_items_scoped(text,text,text,text,integer)
      to watch_runtime;
    grant execute on function public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)
      to watch_runtime;
    grant execute on function public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)
      to watch_runtime;
    grant execute on function public.approve_knowledge_candidates_batch(text[],text,text)
      to watch_runtime;
  end if;
end
$grant$;

commit;
