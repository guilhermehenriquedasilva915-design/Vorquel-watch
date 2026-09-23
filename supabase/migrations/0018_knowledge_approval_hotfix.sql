-- Hotfix V0.2 approval/rejection state updates.
-- RETURNS TABLE output columns are PL/pgSQL variables, so qualify candidate_id.

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

  update vorquel_knowledge.knowledge_candidates as c
  set status = 'APPROVED'
  where c.candidate_id = p_candidate_id;

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

  update vorquel_knowledge.knowledge_candidates as c
  set status = 'REJECTED'
  where c.candidate_id = p_candidate_id;

  return query
  select c.candidate_id, c.status
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = p_candidate_id;
end
$$;
