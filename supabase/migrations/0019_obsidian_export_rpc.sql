-- Vorquel Knowledge Store V0.3: bounded export view for Obsidian.
-- Read-only RPC. Returns approved knowledge plus provenance, never secrets,
-- raw media paths, raw transcript text, or client-controlled instructions.

create or replace function public.export_approved_knowledge(
  p_limit integer default 500,
  p_offset integer default 0
)
returns table (
  knowledge_id text,
  candidate_id text,
  source_id text,
  knowledge_type text,
  domain text,
  title text,
  summary text,
  epistemic_status text,
  data_trust_class text,
  instruction_authority text,
  approved_at timestamptz,
  provenance jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    i.knowledge_id,
    i.candidate_id,
    i.source_id,
    i.knowledge_type,
    i.domain,
    i.title,
    i.summary,
    i.epistemic_status,
    i.data_trust_class,
    i.instruction_authority,
    i.approved_at,
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'source_id', ks.source_id,
          'transcript_id', ks.transcript_id,
          'segment_id', ks.segment_id,
          'screen_observation_id', ks.screen_observation_id,
          'start_ms', ks.start_ms,
          'end_ms', ks.end_ms
        )
        order by ks.start_ms nulls last, ks.end_ms nulls last
      ) filter (where ks.knowledge_source_id is not null),
      '[]'::jsonb
    ) as provenance
  from vorquel_knowledge.knowledge_items i
  left join vorquel_knowledge.knowledge_sources ks
    on ks.knowledge_id = i.knowledge_id
  group by
    i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
    i.domain, i.title, i.summary, i.epistemic_status,
    i.data_trust_class, i.instruction_authority, i.approved_at
  order by i.approved_at desc, i.knowledge_id
  limit least(greatest(coalesce(p_limit, 500), 1), 1000)
  offset greatest(coalesce(p_offset, 0), 0);
$$;

revoke all on function public.export_approved_knowledge(integer, integer)
  from public, anon, authenticated;

grant execute on function public.export_approved_knowledge(integer, integer)
  to service_role, watch_runtime;
