-- Vorquel Brain V1.2
-- First-class withdrawal, temporal supersession, and extractive synthesis context.
-- External-derived content remains reference data only; instruction authority stays NONE.

alter table vorquel_knowledge.knowledge_items
  add column if not exists lifecycle_state text not null default 'ACTIVE',
  add column if not exists valid_from timestamptz,
  add column if not exists valid_until timestamptz,
  add column if not exists superseded_by text,
  add column if not exists withdrawn_at timestamptz,
  add column if not exists withdrawal_reason text;

update vorquel_knowledge.knowledge_items
set valid_from = approved_at
where valid_from is null;

alter table vorquel_knowledge.knowledge_items
  alter column valid_from set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'knowledge_items_lifecycle_state_check'
  ) then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_lifecycle_state_check
      check (lifecycle_state in ('ACTIVE','SUPERSEDED','WITHDRAWN'));
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'knowledge_items_superseded_by_fkey'
  ) then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_superseded_by_fkey
      foreign key (superseded_by)
      references vorquel_knowledge.knowledge_items(knowledge_id);
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'knowledge_items_no_self_supersession_check'
  ) then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_no_self_supersession_check
      check (superseded_by is null or superseded_by <> knowledge_id);
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'knowledge_items_temporal_window_check'
  ) then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_temporal_window_check
      check (valid_until is null or valid_until >= valid_from);
  end if;
end
$$;

create index if not exists knowledge_items_lifecycle_state_idx
  on vorquel_knowledge.knowledge_items(lifecycle_state, valid_from desc);

create index if not exists knowledge_items_superseded_by_idx
  on vorquel_knowledge.knowledge_items(superseded_by)
  where superseded_by is not null;

create table if not exists vorquel_knowledge.knowledge_events (
  knowledge_event_id text primary key,
  knowledge_id text not null
    references vorquel_knowledge.knowledge_items(knowledge_id),
  event_type text not null
    check (event_type in ('WITHDRAW','SUPERSEDE')),
  replacement_knowledge_id text
    references vorquel_knowledge.knowledge_items(knowledge_id),
  actor_type text not null default 'HUMAN'
    check (actor_type = 'HUMAN'),
  reason text not null,
  created_at timestamptz not null default now(),
  constraint knowledge_events_id_format_check
    check (knowledge_event_id ~ '^kev_[a-z0-9]{3,80}$'),
  constraint knowledge_events_reason_len_check
    check (char_length(reason) between 1 and 4000),
  constraint knowledge_events_shape_check
    check (
      (event_type = 'WITHDRAW' and replacement_knowledge_id is null)
      or
      (event_type = 'SUPERSEDE' and replacement_knowledge_id is not null)
    )
);

create index if not exists knowledge_events_knowledge_id_idx
  on vorquel_knowledge.knowledge_events(knowledge_id, created_at desc);

alter table vorquel_knowledge.knowledge_events enable row level security;
revoke all on table vorquel_knowledge.knowledge_events from public, anon, authenticated;
revoke all on table vorquel_knowledge.knowledge_events from watch_runtime;
grant select on table vorquel_knowledge.knowledge_events to service_role;

create or replace function public.withdraw_knowledge_item(
  p_knowledge_id text,
  p_event_id text,
  p_reason text
)
returns table (
  knowledge_id text,
  lifecycle_state text,
  valid_from timestamptz,
  valid_until timestamptz,
  withdrawn_at timestamptz,
  superseded_by text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item vorquel_knowledge.knowledge_items%rowtype;
  v_now timestamptz := now();
begin
  if char_length(trim(coalesce(p_reason,''))) < 1
     or char_length(p_reason) > 4000 then
    raise exception 'reason is required and must be <= 4000 chars';
  end if;

  select * into v_item
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id
  for update;

  if not found then
    raise exception 'knowledge item not found';
  end if;

  if v_item.lifecycle_state = 'WITHDRAWN' then
    return query
    select i.knowledge_id, i.lifecycle_state, i.valid_from, i.valid_until,
           i.withdrawn_at, i.superseded_by
    from vorquel_knowledge.knowledge_items i
    where i.knowledge_id = p_knowledge_id;
    return;
  end if;

  if v_item.lifecycle_state <> 'ACTIVE' then
    raise exception 'only active knowledge can be withdrawn';
  end if;

  insert into vorquel_knowledge.knowledge_events(
    knowledge_event_id, knowledge_id, event_type, actor_type, reason
  ) values (
    p_event_id, p_knowledge_id, 'WITHDRAW', 'HUMAN', trim(p_reason)
  );

  update vorquel_knowledge.knowledge_items i
  set lifecycle_state = 'WITHDRAWN',
      valid_until = v_now,
      withdrawn_at = v_now,
      withdrawal_reason = trim(p_reason)
  where i.knowledge_id = p_knowledge_id;

  return query
  select i.knowledge_id, i.lifecycle_state, i.valid_from, i.valid_until,
         i.withdrawn_at, i.superseded_by
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id;
end
$$;

create or replace function public.supersede_knowledge_item(
  p_knowledge_id text,
  p_replacement_knowledge_id text,
  p_event_id text,
  p_reason text
)
returns table (
  knowledge_id text,
  lifecycle_state text,
  valid_from timestamptz,
  valid_until timestamptz,
  withdrawn_at timestamptz,
  superseded_by text
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item vorquel_knowledge.knowledge_items%rowtype;
  v_replacement vorquel_knowledge.knowledge_items%rowtype;
  v_now timestamptz := now();
begin
  if p_knowledge_id = p_replacement_knowledge_id then
    raise exception 'knowledge item cannot supersede itself';
  end if;
  if char_length(trim(coalesce(p_reason,''))) < 1
     or char_length(p_reason) > 4000 then
    raise exception 'reason is required and must be <= 4000 chars';
  end if;

  select * into v_item
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id
  for update;

  if not found then
    raise exception 'knowledge item not found';
  end if;

  select * into v_replacement
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_replacement_knowledge_id
  for update;

  if not found then
    raise exception 'replacement knowledge item not found';
  end if;

  if v_item.lifecycle_state = 'SUPERSEDED'
     and v_item.superseded_by = p_replacement_knowledge_id then
    return query
    select i.knowledge_id, i.lifecycle_state, i.valid_from, i.valid_until,
           i.withdrawn_at, i.superseded_by
    from vorquel_knowledge.knowledge_items i
    where i.knowledge_id = p_knowledge_id;
    return;
  end if;

  if v_item.lifecycle_state <> 'ACTIVE' then
    raise exception 'only active knowledge can be superseded';
  end if;
  if v_replacement.lifecycle_state <> 'ACTIVE' then
    raise exception 'replacement knowledge must be active';
  end if;

  insert into vorquel_knowledge.knowledge_events(
    knowledge_event_id, knowledge_id, event_type,
    replacement_knowledge_id, actor_type, reason
  ) values (
    p_event_id, p_knowledge_id, 'SUPERSEDE',
    p_replacement_knowledge_id, 'HUMAN', trim(p_reason)
  );

  update vorquel_knowledge.knowledge_items i
  set lifecycle_state = 'SUPERSEDED',
      valid_until = v_now,
      superseded_by = p_replacement_knowledge_id
  where i.knowledge_id = p_knowledge_id;

  return query
  select i.knowledge_id, i.lifecycle_state, i.valid_from, i.valid_until,
         i.withdrawn_at, i.superseded_by
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id;
end
$$;

drop function if exists public.search_knowledge_items(text,text,integer);

create function public.search_knowledge_items(
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
  lifecycle_state text,
  valid_from timestamptz,
  valid_until timestamptz,
  superseded_by text,
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
    i.lifecycle_state, i.valid_from, i.valid_until, i.superseded_by,
    ts_rank_cd(
      i.search_vector,
      plainto_tsquery('simple', left(coalesce(p_query, ''), 500))
    )::real as rank
  from vorquel_knowledge.knowledge_items i
  where char_length(trim(coalesce(p_query, ''))) > 0
    and i.lifecycle_state = 'ACTIVE'
    and (p_domain is null or i.domain = p_domain)
    and i.search_vector @@ plainto_tsquery(
      'simple',
      left(coalesce(p_query, ''), 500)
    )
  order by rank desc, i.approved_at desc
  limit least(greatest(coalesce(p_limit, 20), 1), 50);
$$;

create or replace function public.synthesize_knowledge_context(
  p_query text,
  p_domain text default null,
  p_limit integer default 8
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
with hits as (
  select
    i.knowledge_id, i.source_id, i.knowledge_type, i.domain, i.title,
    i.summary, i.epistemic_status, i.approved_at, i.valid_from,
    ts_rank_cd(
      i.search_vector,
      plainto_tsquery('simple', left(coalesce(p_query, ''), 500))
    )::real as rank
  from vorquel_knowledge.knowledge_items i
  where char_length(trim(coalesce(p_query, ''))) > 0
    and i.lifecycle_state = 'ACTIVE'
    and (p_domain is null or i.domain = p_domain)
    and i.search_vector @@ plainto_tsquery(
      'simple',
      left(coalesce(p_query, ''), 500)
    )
  order by rank desc, i.approved_at desc
  limit least(greatest(coalesce(p_limit, 8), 1), 12)
),
citations as (
  select
    h.knowledge_id,
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'knowledge_source_id', ks.knowledge_source_id,
          'source_id', ks.source_id,
          'transcript_id', ks.transcript_id,
          'segment_id', ks.segment_id,
          'screen_observation_id', ks.screen_observation_id,
          'start_ms', ks.start_ms,
          'end_ms', ks.end_ms
        )
        order by ks.start_ms nulls last
      ) filter (where ks.knowledge_source_id is not null),
      '[]'::jsonb
    ) as provenance
  from hits h
  left join vorquel_knowledge.knowledge_sources ks
    on ks.knowledge_id = h.knowledge_id
  group by h.knowledge_id
)
select jsonb_build_object(
  'query', p_query,
  'domain', p_domain,
  'mode', 'EXTRACTIVE_V1',
  'answer', case
    when count(*) = 0 then null
    else string_agg(
      '- ' || h.title || ': ' || h.summary ||
      ' [knowledge_id=' || h.knowledge_id || ']',
      E'\n'
      order by h.rank desc, h.approved_at desc
    )
  end,
  'items', coalesce(
    jsonb_agg(
      jsonb_build_object(
        'knowledge_id', h.knowledge_id,
        'source_id', h.source_id,
        'knowledge_type', h.knowledge_type,
        'domain', h.domain,
        'title', h.title,
        'summary', h.summary,
        'epistemic_status', h.epistemic_status,
        'approved_at', h.approved_at,
        'valid_from', h.valid_from,
        'rank', h.rank,
        'provenance', c.provenance
      )
      order by h.rank desc, h.approved_at desc
    ),
    '[]'::jsonb
  ),
  'gaps', case
    when count(*) = 0 then jsonb_build_array(
      'Nenhum conhecimento ativo correspondente foi encontrado.'
    )
    when count(*) < 2 then jsonb_build_array(
      'A resposta está apoiada por menos de dois knowledge_items ativos; procure evidência adicional antes de generalizar.'
    )
    else '[]'::jsonb
  end,
  'contains_untrusted_content', true,
  'instruction_authority', 'NONE'
)
from hits h
left join citations c on c.knowledge_id = h.knowledge_id;
$$;

drop function if exists public.export_approved_knowledge(integer,integer);

create function public.export_approved_knowledge(
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
  lifecycle_state text,
  valid_from timestamptz,
  valid_until timestamptz,
  superseded_by text,
  provenance jsonb
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
    i.domain, i.title, i.summary, i.epistemic_status,
    i.data_trust_class, i.instruction_authority, i.approved_at,
    i.lifecycle_state, i.valid_from, i.valid_until, i.superseded_by,
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
  where i.lifecycle_state = 'ACTIVE'
  group by
    i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
    i.domain, i.title, i.summary, i.epistemic_status,
    i.data_trust_class, i.instruction_authority, i.approved_at,
    i.lifecycle_state, i.valid_from, i.valid_until, i.superseded_by
  order by i.approved_at desc, i.knowledge_id
  limit least(greatest(coalesce(p_limit, 500), 1), 1000)
  offset greatest(coalesce(p_offset, 0), 0);
$$;

revoke all on function public.withdraw_knowledge_item(text,text,text)
  from public, anon, authenticated;
revoke all on function public.supersede_knowledge_item(text,text,text,text)
  from public, anon, authenticated;
revoke all on function public.synthesize_knowledge_context(text,text,integer)
  from public, anon, authenticated;
revoke all on function public.search_knowledge_items(text,text,integer)
  from public, anon, authenticated;
revoke all on function public.export_approved_knowledge(integer,integer)
  from public, anon, authenticated;

grant execute on function public.withdraw_knowledge_item(text,text,text)
  to service_role, watch_runtime;
grant execute on function public.supersede_knowledge_item(text,text,text,text)
  to service_role, watch_runtime;
grant execute on function public.synthesize_knowledge_context(text,text,integer)
  to service_role, watch_runtime;
grant execute on function public.search_knowledge_items(text,text,integer)
  to service_role, watch_runtime;
grant execute on function public.export_approved_knowledge(integer,integer)
  to service_role, watch_runtime;
