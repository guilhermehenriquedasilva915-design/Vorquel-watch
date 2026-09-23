\set ON_ERROR_STOP on

begin;

insert into public.sources (
  source_id, source_kind, content_sha256, byte_size, detected_mime,
  duration_ms, ingest_status, container, has_video, has_audio,
  video_stream_count, audio_stream_count
) values (
  'src_knowledge_integrity', 'LOCAL_VIDEO', repeat('a', 64), 1024,
  'video/mp4', 180000, 'READY', 'mp4', true, true, 1, 1
);

select * from public.create_knowledge_candidate(
  'knd_integrity_primary',
  'src_knowledge_integrity',
  'PROCEDURE',
  'n8n',
  'Bounded retry integrity',
  'Use bounded retries and preserve the original failure evidence.',
  'OBSERVADO',
  repeat('b', 64),
  '[{"knowledge_source_id":"ksr_integrity_primary","start_ms":1000,"end_ms":9000}]'::jsonb
);

do $$
begin
  if not exists (
    select 1 from vorquel_knowledge.knowledge_candidates
    where candidate_id = 'knd_integrity_primary'
      and status = 'PENDING'
      and data_trust_class = 'UNTRUSTED_DERIVED'
      and instruction_authority = 'NONE'
  ) then
    raise exception 'candidate proposal/trust invariant failed';
  end if;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_sources
    where candidate_id = 'knd_integrity_primary'
      and source_id = 'src_knowledge_integrity'
      and start_ms = 1000
      and end_ms = 9000
  ) then
    raise exception 'candidate provenance was not linked';
  end if;
end
$$;

select * from public.approve_knowledge_candidate(
  'knd_integrity_primary',
  'krv_integrity_primary',
  'knw_integrity_primary',
  'Human-reviewed integrity acceptance.'
);

do $$
declare
  v_context jsonb;
begin
  if not exists (
    select 1 from vorquel_knowledge.knowledge_items
    where knowledge_id = 'knw_integrity_primary'
      and lifecycle_state = 'ACTIVE'
      and valid_from is not null
      and valid_from = approved_at
      and data_trust_class = 'UNTRUSTED_DERIVED'
      and instruction_authority = 'NONE'
  ) then
    raise exception 'approval did not create valid ACTIVE knowledge';
  end if;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_reviews
    where knowledge_review_id = 'krv_integrity_primary'
      and candidate_id = 'knd_integrity_primary'
      and action = 'APPROVE'
      and actor_type = 'HUMAN'
  ) then
    raise exception 'human approval review was not recorded';
  end if;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_candidates
    where candidate_id = 'knd_integrity_primary' and status = 'APPROVED'
  ) then
    raise exception 'approved candidate status was not persisted';
  end if;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_sources
    where knowledge_id = 'knw_integrity_primary'
      and source_id = 'src_knowledge_integrity'
      and start_ms = 1000
      and end_ms = 9000
  ) then
    raise exception 'approval did not copy provenance to the knowledge item';
  end if;

  if not exists (
    select 1 from public.search_knowledge_items('bounded retry', 'n8n', 20)
    where knowledge_id = 'knw_integrity_primary'
      and lifecycle_state = 'ACTIVE'
      and valid_from is not null
      and instruction_authority = 'NONE'
  ) then
    raise exception 'approved knowledge is not searchable';
  end if;

  select public.synthesize_knowledge_context('bounded retry', 'n8n', 8)
    into v_context;

  if v_context->>'instruction_authority' <> 'NONE'
     or v_context->>'contains_untrusted_content' <> 'true'
     or not (v_context->'items' @> '[{"knowledge_id":"knw_integrity_primary"}]'::jsonb) then
    raise exception 'synthesis trust contract failed';
  end if;

  if lower(v_context::text) ~ '"(path|filepath|directory|workspace|secret|credential|bytes|base64)"[[:space:]]*:' then
    raise exception 'synthesis exposed a forbidden field';
  end if;
end
$$;

select * from public.create_knowledge_candidate(
  'knd_integrity_replacement', 'src_knowledge_integrity', 'PROCEDURE', 'n8n',
  'Bounded retry integrity v2',
  'Use bounded retries with an explicit terminal failure record.',
  'OBSERVADO', repeat('c', 64),
  '[{"knowledge_source_id":"ksr_integrity_replacement","start_ms":10000,"end_ms":18000}]'::jsonb
);
select * from public.approve_knowledge_candidate(
  'knd_integrity_replacement', 'krv_integrity_replacement',
  'knw_integrity_replacement', 'Human-reviewed replacement.'
);
select * from public.supersede_knowledge_item(
  'knw_integrity_primary', 'knw_integrity_replacement',
  'kev_integritysupersede', 'Replaced by the reviewed v2 procedure.'
);

select * from public.create_knowledge_candidate(
  'knd_integrity_withdraw', 'src_knowledge_integrity', 'WARNING', 'n8n',
  'Temporary retry warning',
  'This reviewed warning is intentionally withdrawn during acceptance.',
  'OBSERVADO', repeat('d', 64),
  '[{"knowledge_source_id":"ksr_integrity_withdraw","start_ms":20000,"end_ms":24000}]'::jsonb
);
select * from public.approve_knowledge_candidate(
  'knd_integrity_withdraw', 'krv_integrity_withdraw',
  'knw_integrity_withdraw', 'Human-reviewed withdrawal subject.'
);
select * from public.withdraw_knowledge_item(
  'knw_integrity_withdraw', 'kev_integritywithdraw',
  'Acceptance verifies explicit human withdrawal.'
);

do $$
begin
  if not exists (
    select 1 from vorquel_knowledge.knowledge_items
    where knowledge_id = 'knw_integrity_primary'
      and lifecycle_state = 'SUPERSEDED'
      and superseded_by = 'knw_integrity_replacement'
      and valid_until is not null
  ) then
    raise exception 'supersession lifecycle failed';
  end if;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_items
    where knowledge_id = 'knw_integrity_withdraw'
      and lifecycle_state = 'WITHDRAWN'
      and withdrawn_at is not null
      and valid_until is not null
  ) then
    raise exception 'withdrawal lifecycle failed';
  end if;

  if (select count(*) from vorquel_knowledge.knowledge_events
      where knowledge_event_id in ('kev_integritysupersede', 'kev_integritywithdraw')
        and actor_type = 'HUMAN') <> 2 then
    raise exception 'human lifecycle history was not preserved';
  end if;

  if exists (
    select 1 from public.search_knowledge_items('temporary retry warning', 'n8n', 20)
    where knowledge_id = 'knw_integrity_withdraw'
  ) then
    raise exception 'withdrawn knowledge remained searchable';
  end if;

  if exists (
    select 1 from public.search_knowledge_items('bounded retry integrity', 'n8n', 20)
    where knowledge_id = 'knw_integrity_primary'
  ) then
    raise exception 'superseded knowledge remained searchable';
  end if;
end
$$;

rollback;

select 'knowledge approval integrity: ok' as result;
