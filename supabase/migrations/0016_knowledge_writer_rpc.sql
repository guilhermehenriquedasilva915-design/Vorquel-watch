-- Vorquel Knowledge Store V0.2: controlled writer + retrieval RPC boundary.
--
-- No client writes the private schema directly. These narrowly-scoped
-- SECURITY DEFINER functions are the application boundary. Media-derived text
-- stays data-only with instruction_authority = NONE even after human approval.

-- Strengthen provenance: a segment reference must belong to the same source.
alter table public.transcript_segments
  add constraint transcript_segments_segment_source_uq unique (segment_id, source_id);

alter table vorquel_knowledge.knowledge_sources
  drop constraint knowledge_sources_transcript_id_fkey,
  drop constraint knowledge_sources_segment_id_fkey,
  drop constraint knowledge_sources_screen_observation_id_fkey,
  add constraint knowledge_sources_transcript_source_fkey
    foreign key (transcript_id, source_id)
    references public.transcripts (transcript_id, source_id)
    on delete restrict,
  add constraint knowledge_sources_segment_source_fkey
    foreign key (segment_id, source_id)
    references public.transcript_segments (segment_id, source_id)
    on delete restrict,
  add constraint knowledge_sources_screen_source_fkey
    foreign key (screen_observation_id, source_id)
    references public.screen_observations (observation_id, source_id)
    on delete restrict;

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
  on conflict on constraint knowledge_candidates_source_id_content_hash_key do nothing;

  -- If this exact content already exists for the source, return the existing
  -- candidate rather than duplicating it. Provenance for an existing candidate
  -- is left immutable in this V0.2 path.
  select c.candidate_id into p_candidate_id
  from vorquel_knowledge.knowledge_candidates c
  where c.source_id = p_source_id
    and c.content_hash = p_content_hash
  limit 1;

  -- Only persist provenance on first creation.
  if not exists (
    select 1 from vorquel_knowledge.knowledge_sources ks
    where ks.candidate_id = p_candidate_id
  ) then
    for v_entry in select value from jsonb_array_elements(coalesce(p_provenance, '[]'::jsonb))
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

create or replace function public.approve_knowledge_candidate(
  p_candidate_id text,
  p_review_id text,
  p_knowledge_id text,
  p_note text default null
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
  content_hash text,
  data_trust_class text,
  instruction_authority text,
  approved_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_candidate vorquel_knowledge.knowledge_candidates%rowtype;
  v_existing text;
begin
  select i.knowledge_id into v_existing
  from vorquel_knowledge.knowledge_items i
  where i.candidate_id = p_candidate_id
  limit 1;

  if v_existing is not null then
    return query
    select
      i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
      i.domain, i.title, i.summary, i.epistemic_status, i.content_hash,
      i.data_trust_class, i.instruction_authority, i.approved_at
    from vorquel_knowledge.knowledge_items i
    where i.knowledge_id = v_existing;
    return;
  end if;

  select * into v_candidate
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = p_candidate_id
  for update;

  if not found then
    raise exception 'candidate not found';
  end if;
  if v_candidate.status <> 'PENDING' then
    raise exception 'candidate is not pending';
  end if;

  insert into vorquel_knowledge.knowledge_reviews (
    knowledge_review_id, candidate_id, action, actor_type, note
  ) values (
    p_review_id, p_candidate_id, 'APPROVE', 'HUMAN', p_note
  );

  update vorquel_knowledge.knowledge_candidates
  set status = 'APPROVED'
  where candidate_id = p_candidate_id;

  insert into vorquel_knowledge.knowledge_items (
    knowledge_id, candidate_id, source_id, knowledge_type, domain,
    title, summary, epistemic_status, content_hash,
    data_trust_class, instruction_authority
  ) values (
    p_knowledge_id, v_candidate.candidate_id, v_candidate.source_id,
    v_candidate.knowledge_type, v_candidate.domain, v_candidate.title,
    v_candidate.summary, v_candidate.epistemic_status, v_candidate.content_hash,
    'UNTRUSTED_DERIVED', 'NONE'
  );

  insert into vorquel_knowledge.knowledge_sources (
    knowledge_source_id, knowledge_id, source_id,
    transcript_id, segment_id, screen_observation_id, start_ms, end_ms
  )
  select
    'ksr_' || substr(md5(p_knowledge_id || ':' || ks.knowledge_source_id), 1, 32),
    p_knowledge_id,
    ks.source_id,
    ks.transcript_id,
    ks.segment_id,
    ks.screen_observation_id,
    ks.start_ms,
    ks.end_ms
  from vorquel_knowledge.knowledge_sources ks
  where ks.candidate_id = p_candidate_id;

  return query
  select
    i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
    i.domain, i.title, i.summary, i.epistemic_status, i.content_hash,
    i.data_trust_class, i.instruction_authority, i.approved_at
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id;
end
$$;

create or replace function public.reject_knowledge_candidate(
  p_candidate_id text,
  p_review_id text,
  p_note text default null
)
returns table (
  candidate_id text,
  status text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
begin
  select c.status into v_status
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = p_candidate_id
  for update;

  if v_status is null then
    raise exception 'candidate not found';
  end if;

  if v_status = 'REJECTED' then
    return query
    select c.candidate_id, c.status
    from vorquel_knowledge.knowledge_candidates c
    where c.candidate_id = p_candidate_id;
    return;
  end if;

  if v_status <> 'PENDING' then
    raise exception 'candidate is not pending';
  end if;

  insert into vorquel_knowledge.knowledge_reviews (
    knowledge_review_id, candidate_id, action, actor_type, note
  ) values (
    p_review_id, p_candidate_id, 'REJECT', 'HUMAN', p_note
  );

  update vorquel_knowledge.knowledge_candidates
  set status = 'REJECTED'
  where knowledge_candidates.candidate_id = p_candidate_id;

  return query
  select c.candidate_id, c.status
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = p_candidate_id;
end
$$;

create or replace function public.list_knowledge_candidates(
  p_source_id text default null,
  p_status text default null,
  p_limit integer default 50
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
  data_trust_class text,
  instruction_authority text,
  created_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    c.candidate_id, c.source_id, c.knowledge_type, c.domain, c.title,
    c.summary, c.epistemic_status, c.status,
    c.data_trust_class, c.instruction_authority, c.created_at
  from vorquel_knowledge.knowledge_candidates c
  where (p_source_id is null or c.source_id = p_source_id)
    and (p_status is null or c.status = p_status)
  order by c.created_at desc
  limit least(greatest(coalesce(p_limit, 50), 1), 100);
$$;

create or replace function public.search_knowledge_items(
  p_query text,
  p_domain text default null,
  p_limit integer default 20
)
returns table (
  knowledge_id text,
  source_id text,
  knowledge_type text,
  domain text,
  title text,
  summary text,
  epistemic_status text,
  data_trust_class text,
  instruction_authority text,
  approved_at timestamptz,
  rank real
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    i.knowledge_id, i.source_id, i.knowledge_type, i.domain, i.title,
    i.summary, i.epistemic_status, i.data_trust_class,
    i.instruction_authority, i.approved_at,
    ts_rank_cd(
      i.search_vector,
      plainto_tsquery('simple', left(coalesce(p_query, ''), 500))
    )::real as rank
  from vorquel_knowledge.knowledge_items i
  where char_length(trim(coalesce(p_query, ''))) > 0
    and (p_domain is null or i.domain = p_domain)
    and i.search_vector @@ plainto_tsquery(
      'simple',
      left(coalesce(p_query, ''), 500)
    )
  order by rank desc, i.approved_at desc
  limit least(greatest(coalesce(p_limit, 20), 1), 50);
$$;

revoke all on function public.create_knowledge_candidate(
  text,text,text,text,text,text,text,text,jsonb
) from public, anon, authenticated;
revoke all on function public.approve_knowledge_candidate(
  text,text,text,text
) from public, anon, authenticated;
revoke all on function public.reject_knowledge_candidate(
  text,text,text
) from public, anon, authenticated;
revoke all on function public.list_knowledge_candidates(
  text,text,integer
) from public, anon, authenticated;
revoke all on function public.search_knowledge_items(
  text,text,integer
) from public, anon, authenticated;

grant execute on function public.create_knowledge_candidate(
  text,text,text,text,text,text,text,text,jsonb
) to service_role, watch_runtime;
grant execute on function public.approve_knowledge_candidate(
  text,text,text,text
) to service_role, watch_runtime;
grant execute on function public.reject_knowledge_candidate(
  text,text,text
) to service_role, watch_runtime;
grant execute on function public.list_knowledge_candidates(
  text,text,integer
) to service_role, watch_runtime;
grant execute on function public.search_knowledge_items(
  text,text,integer
) to service_role, watch_runtime;
