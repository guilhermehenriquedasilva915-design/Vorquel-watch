-- Hotfix for the already-applied V0.2 writer function.
-- RETURNS TABLE output names become PL/pgSQL variables, so ON CONFLICT
-- column names were ambiguous. Pin the named unique constraint explicitly.

create or replace function public.create_knowledge_candidate(
  p_candidate_id text,
  p_source_id text,
  p_knowledge_type text,
  p_domain text,
  p_title text,
  p_summary text,
  p_epistemic_status text,
  p_content_hash text,
  p_provenance jsonb default '[]'::jsonb
)
returns table (
  candidate_id text,
  source_id text,
  knowledge_type text,
  domain text,
  title text,
  summary text,
  epistemic_status text,
  status text,
  content_hash text,
  data_trust_class text,
  instruction_authority text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_source public.sources%rowtype;
  v_entry jsonb;
  v_start bigint;
  v_end bigint;
begin
  select * into v_source
  from public.sources s
  where s.source_id = p_source_id
    and s.ingest_status = 'READY';

  if not found then
    raise exception 'source is not ready';
  end if;

  if jsonb_typeof(coalesce(p_provenance, '[]'::jsonb)) <> 'array' then
    raise exception 'provenance must be an array';
  end if;

  if jsonb_array_length(coalesce(p_provenance, '[]'::jsonb)) > 100 then
    raise exception 'too many provenance rows';
  end if;

  insert into vorquel_knowledge.knowledge_candidates (
    candidate_id, source_id, knowledge_type, domain, title, summary,
    epistemic_status, status, content_hash,
    data_trust_class, instruction_authority
  ) values (
    p_candidate_id, p_source_id, p_knowledge_type, p_domain, p_title, p_summary,
    p_epistemic_status, 'PENDING', p_content_hash,
    'UNTRUSTED_DERIVED', 'NONE'
  )
  on conflict on constraint knowledge_candidates_source_id_content_hash_key
  do nothing;

  select c.candidate_id into p_candidate_id
  from vorquel_knowledge.knowledge_candidates c
  where c.source_id = p_source_id
    and c.content_hash = p_content_hash
  limit 1;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_sources ks
    where ks.candidate_id = p_candidate_id
  ) then
    for v_entry in
      select value
      from jsonb_array_elements(coalesce(p_provenance, '[]'::jsonb))
    loop
      if jsonb_typeof(v_entry) <> 'object' then
        raise exception 'invalid provenance row';
      end if;

      v_start := case
        when v_entry ? 'start_ms' and v_entry->>'start_ms' <> ''
        then (v_entry->>'start_ms')::bigint else null end;
      v_end := case
        when v_entry ? 'end_ms' and v_entry->>'end_ms' <> ''
        then (v_entry->>'end_ms')::bigint else null end;

      if v_start is not null and v_source.duration_ms is not null
         and v_start > v_source.duration_ms then
        raise exception 'provenance timestamp exceeds source duration';
      end if;
      if v_end is not null and v_source.duration_ms is not null
         and v_end > v_source.duration_ms then
        raise exception 'provenance timestamp exceeds source duration';
      end if;

      insert into vorquel_knowledge.knowledge_sources (
        knowledge_source_id, candidate_id, source_id,
        transcript_id, segment_id, screen_observation_id,
        start_ms, end_ms
      ) values (
        v_entry->>'knowledge_source_id',
        p_candidate_id,
        p_source_id,
        nullif(v_entry->>'transcript_id', ''),
        nullif(v_entry->>'segment_id', ''),
        nullif(v_entry->>'screen_observation_id', ''),
        v_start,
        v_end
      );
    end loop;
  end if;

  return query
  select
    c.candidate_id, c.source_id, c.knowledge_type, c.domain, c.title,
    c.summary, c.epistemic_status, c.status, c.content_hash,
    c.data_trust_class, c.instruction_authority, c.created_at
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = p_candidate_id;
end
$$;
