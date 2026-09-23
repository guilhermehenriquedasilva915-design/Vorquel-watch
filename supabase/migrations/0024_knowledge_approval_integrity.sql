-- Vorquel Brain V1.2 approval integrity.
--
-- Migrations 0021-0023 are reserved by the parallel scoped-knowledge lineage.
-- This migration therefore uses 0024 and remains safe when that lineage is
-- integrated: 0023 already provides the richer, scope-aware approval RPC.
--
-- The legacy approval RPC must explicitly populate valid_from because 0020
-- made that column NOT NULL without defining a table-wide default.

do $migration$
begin
  if not exists (
    select 1
    from information_schema.columns
    where table_schema = 'vorquel_knowledge'
      and table_name = 'knowledge_candidates'
      and column_name = 'scope_type'
  ) then
    execute $definition$
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
      as $function$
      declare
        v_candidate vorquel_knowledge.knowledge_candidates%rowtype;
        v_existing text;
        v_approved_at timestamptz := now();
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
          data_trust_class, instruction_authority, approved_at, valid_from
        ) values (
          p_knowledge_id, v_candidate.candidate_id, v_candidate.source_id,
          v_candidate.knowledge_type, v_candidate.domain, v_candidate.title,
          v_candidate.summary, v_candidate.epistemic_status,
          v_candidate.content_hash, 'UNTRUSTED_DERIVED', 'NONE',
          v_approved_at, v_approved_at
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
      $function$;
    $definition$;
  else
    raise notice 'scope-aware approval RPC already installed; preserving it';
  end if;
end
$migration$;

revoke all on function public.approve_knowledge_candidate(text,text,text,text)
  from public, anon, authenticated;

grant execute on function public.approve_knowledge_candidate(text,text,text,text)
  to service_role, watch_runtime;

comment on function public.approve_knowledge_candidate(text,text,text,text) is
  'Human approval creates ACTIVE knowledge with explicit valid_from and copied provenance; external-derived content keeps instruction authority NONE.';
