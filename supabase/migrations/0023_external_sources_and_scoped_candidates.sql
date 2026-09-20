-- 0023: external (non-media) sources, source versioning, scoped candidates
--
-- Three things block LEARN today:
--
--   1. public.sources only admits LOCAL_VIDEO/LOCAL_AUDIO with
--      data_trust_class = 'UNTRUSTED_MEDIA'. A PDF, a repo file, a message or
--      an n8n workflow cannot be registered as a source at all.
--   2. create_knowledge_candidate carries no scope and no classification, so
--      client knowledge cannot be created even after 0021 added the columns.
--   3. approve_knowledge_candidate copies only the media locator columns. After
--      0021 it would silently DROP locator_kind/locator on approval, losing the
--      provenance of every non-media source at the moment it becomes knowledge.
--
-- (3) is a regression introduced by 0021 and is fixed here.
--
-- Additive: the media path keeps working unchanged, and the old
-- create_knowledge_candidate signature is left in place for existing callers.

begin;

-- ---------------------------------------------------------------------------
-- 1. Sources: admit non-media kinds
-- ---------------------------------------------------------------------------

alter table public.sources drop constraint if exists sources_source_kind_check;
alter table public.sources
  add constraint sources_source_kind_check
  check (source_kind in (
    'LOCAL_VIDEO','LOCAL_AUDIO','YOUTUBE',
    'MESSAGE','TEXT','MARKDOWN','JSON','PDF','DOCX',
    'GIT_REPO','N8N_WORKFLOW'));

alter table public.sources drop constraint if exists sources_data_trust_class_check;
alter table public.sources
  add constraint sources_data_trust_class_check
  check (data_trust_class in (
    'UNTRUSTED_MEDIA','UNTRUSTED_DOCUMENT','UNTRUSTED_REPO',
    'UNTRUSTED_MESSAGE','UNTRUSTED_WORKFLOW'));

-- A document has no duration. The existing provenance bound check already
-- reads `duration_ms is not null`, so relaxing this is safe for media too.
alter table public.sources alter column duration_ms drop not null;
alter table public.sources alter column container   drop not null;

-- Media kinds must still declare a duration; only the new kinds may omit it.
alter table public.sources drop constraint if exists sources_media_duration_check;
alter table public.sources
  add constraint sources_media_duration_check
  check (
    source_kind not in ('LOCAL_VIDEO','LOCAL_AUDIO')
    or duration_ms is not null
  );

-- ---------------------------------------------------------------------------
-- 2. Sources: scope, classification and versioning
-- ---------------------------------------------------------------------------

alter table public.sources
  add column if not exists scope_type text not null default 'GLOBAL_VORQUEL',
  add column if not exists scope_id   text not null default 'GLOBAL',
  add column if not exists data_classification text not null default 'INTERNAL',
  -- Stable identity of a source across revisions: the same PDF re-exported, the
  -- same repo at a later commit. content_sha256 identifies the bytes;
  -- logical_key identifies the thing.
  add column if not exists logical_key text,
  add column if not exists source_version integer not null default 1,
  add column if not exists supersedes_source_id text;

do $mig$
begin
  if not exists (select 1 from pg_constraint where conname = 'sources_scope_type_check') then
    alter table public.sources add constraint sources_scope_type_check
      check (scope_type in ('GLOBAL_VORQUEL','CLIENT','PROJECT','PRIVATE_TEST'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sources_scope_id_check') then
    alter table public.sources add constraint sources_scope_id_check
      check (scope_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$');
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sources_scope_shape_check') then
    alter table public.sources add constraint sources_scope_shape_check
      check ((scope_type = 'GLOBAL_VORQUEL') = (scope_id = 'GLOBAL'));
  end if;
  if not exists (select 1 from pg_constraint
                 where conname = 'sources_data_classification_check') then
    alter table public.sources add constraint sources_data_classification_check
      check (data_classification in ('PUBLIC','INTERNAL','CLIENT_CONFIDENTIAL','PII'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sources_version_check') then
    alter table public.sources add constraint sources_version_check
      check (source_version >= 1);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sources_no_self_supersede_check') then
    alter table public.sources add constraint sources_no_self_supersede_check
      check (supersedes_source_id is null or supersedes_source_id <> source_id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'sources_supersedes_fkey') then
    alter table public.sources add constraint sources_supersedes_fkey
      foreign key (supersedes_source_id) references public.sources(source_id)
      on delete set null;
  end if;
end
$mig$;

-- One row per (logical identity, version). History is preserved: a new version
-- is a new row pointing at the previous one, never an overwrite.
create unique index if not exists sources_logical_version_uidx
  on public.sources (logical_key, source_version)
  where logical_key is not null;

create index if not exists sources_scope_idx
  on public.sources (scope_type, scope_id, source_kind);

-- ---------------------------------------------------------------------------
-- 3. Register an external source
-- ---------------------------------------------------------------------------
-- Idempotent on bytes: the same content_sha256 returns the existing row
-- untouched. Different bytes under the same logical_key create the next
-- version and link back.

create or replace function public.register_external_source(
  p_source_id           text,
  p_source_kind         text,
  p_content_sha256      text,
  p_byte_size           bigint,
  p_detected_mime       text,
  p_logical_key         text default null,
  p_scope_type          text default 'GLOBAL_VORQUEL',
  p_scope_id            text default 'GLOBAL',
  p_data_classification text default 'INTERNAL',
  p_external_metadata   jsonb default '{}'::jsonb
)
returns table (
  source_id text, source_kind text, content_sha256 text,
  logical_key text, source_version integer, supersedes_source_id text,
  scope_type text, scope_id text, data_classification text,
  ingest_status text, created_at timestamptz, reused boolean
)
language plpgsql
security definer
set search_path to ''
as $fn$
declare
  v_existing public.sources%rowtype;
  v_trust    text;
  v_version  integer := 1;
  v_prev     text;
begin
  if p_source_kind in ('LOCAL_VIDEO','LOCAL_AUDIO') then
    raise exception 'use the media ingest path for media sources'
      using errcode = '22023';
  end if;

  v_trust := case p_source_kind
    when 'GIT_REPO'     then 'UNTRUSTED_REPO'
    when 'N8N_WORKFLOW' then 'UNTRUSTED_WORKFLOW'
    when 'MESSAGE'      then 'UNTRUSTED_MESSAGE'
    else 'UNTRUSTED_DOCUMENT'
  end;

  -- Same bytes: nothing new was learned, so nothing is written.
  select * into v_existing
  from public.sources s
  where s.content_sha256 = p_content_sha256;

  if found then
    return query
    select v_existing.source_id, v_existing.source_kind, v_existing.content_sha256,
           v_existing.logical_key, v_existing.source_version,
           v_existing.supersedes_source_id,
           v_existing.scope_type, v_existing.scope_id, v_existing.data_classification,
           v_existing.ingest_status, v_existing.created_at, true;
    return;
  end if;

  if p_logical_key is not null then
    select s.source_version, s.source_id into v_version, v_prev
    from public.sources s
    where s.logical_key = p_logical_key
    order by s.source_version desc
    limit 1;

    if found then
      v_version := v_version + 1;
    else
      v_version := 1;
      v_prev := null;
    end if;
  end if;

  insert into public.sources (
    source_id, source_kind, content_sha256, byte_size, detected_mime,
    duration_ms, ingest_status, external_metadata,
    data_trust_class, instruction_authority,
    scope_type, scope_id, data_classification,
    logical_key, source_version, supersedes_source_id
  ) values (
    p_source_id, p_source_kind, p_content_sha256, p_byte_size, p_detected_mime,
    null, 'READY', coalesce(p_external_metadata, '{}'::jsonb),
    v_trust, 'NONE',
    p_scope_type, p_scope_id, p_data_classification,
    p_logical_key, v_version, v_prev
  );

  return query
  select s.source_id, s.source_kind, s.content_sha256,
         s.logical_key, s.source_version, s.supersedes_source_id,
         s.scope_type, s.scope_id, s.data_classification,
         s.ingest_status, s.created_at, false
  from public.sources s
  where s.source_id = p_source_id;
end
$fn$;

-- ---------------------------------------------------------------------------
-- 4. Scoped candidate creation with generic provenance
-- ---------------------------------------------------------------------------

create or replace function public.create_knowledge_candidate_scoped(
  p_candidate_id        text,
  p_source_id           text,
  p_knowledge_type      text,
  p_domain              text,
  p_title               text,
  p_summary             text,
  p_epistemic_status    text,
  p_content_hash        text,
  p_scope_type          text default 'GLOBAL_VORQUEL',
  p_scope_id            text default 'GLOBAL',
  p_data_classification text default 'INTERNAL',
  p_provenance          jsonb default '[]'::jsonb
)
returns table (
  candidate_id text, source_id text, knowledge_type text, domain text,
  title text, summary text, epistemic_status text, status text,
  content_hash text, scope_type text, scope_id text, data_classification text,
  instruction_authority text, created_at timestamptz, reused boolean
)
language plpgsql
security definer
set search_path to ''
as $fn$
declare
  v_source    public.sources%rowtype;
  v_entry     jsonb;
  v_start     bigint;
  v_end       bigint;
  v_kind      text;
  v_locator   jsonb;
  v_resolved  text;
  -- A bare marker rather than the returned candidate_id: that name is also an
  -- OUT column of this function, and `returning candidate_id` is ambiguous
  -- between the two. `returning 1` references no column at all.
  v_inserted  integer;
  v_reused    boolean := false;
begin
  select * into v_source
  from public.sources s
  where s.source_id = p_source_id and s.ingest_status = 'READY';

  if not found then
    raise exception 'source is not ready' using errcode = '22023';
  end if;

  -- A candidate may never be filed under a scope the source does not belong
  -- to. Otherwise LEARN becomes a way to launder one client's material into
  -- another scope.
  if v_source.scope_type <> p_scope_type or v_source.scope_id <> p_scope_id then
    raise exception 'candidate scope does not match source scope'
      using errcode = '22023';
  end if;

  if p_data_classification = 'SECRET' then
    raise exception 'SECRET must never become knowledge' using errcode = '22023';
  end if;

  if jsonb_typeof(coalesce(p_provenance, '[]'::jsonb)) <> 'array' then
    raise exception 'provenance must be an array' using errcode = '22023';
  end if;
  if jsonb_array_length(coalesce(p_provenance, '[]'::jsonb)) > 100 then
    raise exception 'too many provenance rows' using errcode = '22023';
  end if;

  insert into vorquel_knowledge.knowledge_candidates (
    candidate_id, source_id, knowledge_type, domain, title, summary,
    epistemic_status, status, content_hash,
    data_trust_class, instruction_authority,
    scope_type, scope_id, data_classification
  ) values (
    p_candidate_id, p_source_id, p_knowledge_type, p_domain, p_title, p_summary,
    p_epistemic_status, 'PENDING', p_content_hash,
    'UNTRUSTED_DERIVED', 'NONE',
    p_scope_type, p_scope_id, p_data_classification
  )
  on conflict on constraint knowledge_candidates_source_id_content_hash_key
  do nothing
  returning 1 into v_inserted;

  -- Same source + same content hash is the same candidate. Re-learning a
  -- source must not multiply its candidates.
  select c.candidate_id into v_resolved
  from vorquel_knowledge.knowledge_candidates c
  where c.source_id = p_source_id and c.content_hash = p_content_hash
  limit 1;

  -- Reuse is decided by whether this call inserted anything, not by comparing
  -- ids: a caller that derives candidate_id from the content passes the same id
  -- on every replay, and an id comparison would report every replay as new.
  v_reused := v_inserted is null;

  if not exists (
    select 1 from vorquel_knowledge.knowledge_sources ks
    where ks.candidate_id = v_resolved
  ) then
    for v_entry in
      select value from jsonb_array_elements(coalesce(p_provenance, '[]'::jsonb))
    loop
      if jsonb_typeof(v_entry) <> 'object' then
        raise exception 'invalid provenance row' using errcode = '22023';
      end if;

      v_kind := coalesce(nullif(v_entry->>'locator_kind', ''), 'GENERIC');
      v_locator := coalesce(v_entry->'locator', '{}'::jsonb);
      if jsonb_typeof(v_locator) <> 'object' then
        raise exception 'locator must be an object' using errcode = '22023';
      end if;

      v_start := case when v_entry ? 'start_ms' and v_entry->>'start_ms' <> ''
                      then (v_entry->>'start_ms')::bigint else null end;
      v_end   := case when v_entry ? 'end_ms' and v_entry->>'end_ms' <> ''
                      then (v_entry->>'end_ms')::bigint else null end;

      if v_start is not null and v_source.duration_ms is not null
         and v_start > v_source.duration_ms then
        raise exception 'provenance timestamp exceeds source duration'
          using errcode = '22023';
      end if;
      if v_end is not null and v_source.duration_ms is not null
         and v_end > v_source.duration_ms then
        raise exception 'provenance timestamp exceeds source duration'
          using errcode = '22023';
      end if;

      insert into vorquel_knowledge.knowledge_sources (
        knowledge_source_id, candidate_id, source_id,
        transcript_id, segment_id, screen_observation_id,
        start_ms, end_ms, locator_kind, locator
      ) values (
        v_entry->>'knowledge_source_id',
        v_resolved,
        p_source_id,
        nullif(v_entry->>'transcript_id', ''),
        nullif(v_entry->>'segment_id', ''),
        nullif(v_entry->>'screen_observation_id', ''),
        v_start, v_end, v_kind, v_locator
      );
    end loop;
  end if;

  return query
  select c.candidate_id, c.source_id, c.knowledge_type, c.domain, c.title,
         c.summary, c.epistemic_status, c.status, c.content_hash,
         c.scope_type, c.scope_id, c.data_classification,
         c.instruction_authority, c.created_at, v_reused
  from vorquel_knowledge.knowledge_candidates c
  where c.candidate_id = v_resolved;
end
$fn$;

-- ---------------------------------------------------------------------------
-- 5. Approval must carry scope, classification and the generic locator
-- ---------------------------------------------------------------------------
-- Replaces the 0016/0017/0018 body. Without this, 0021's locator columns are
-- dropped exactly when a candidate becomes knowledge.
--
-- It also repairs a second, older break. 0020 added valid_from, backfilled the
-- existing rows and made the column NOT NULL -- but gave it no default and did
-- not teach the approval path to set it. Every approval attempted since 0020
-- therefore fails on a not-null violation. No CI job in this repository applied
-- a migration until now, which is why it stayed invisible.
--
-- The default is set on the column as well as in the function below, so any
-- other writer is covered too rather than only this one path.

alter table vorquel_knowledge.knowledge_items
  alter column valid_from set default now();

create or replace function public.approve_knowledge_candidate(
  p_candidate_id text,
  p_review_id    text,
  p_knowledge_id text,
  p_note         text default null
)
returns table (
  knowledge_id text, candidate_id text, source_id text, knowledge_type text,
  domain text, title text, summary text, epistemic_status text,
  content_hash text, data_trust_class text, instruction_authority text,
  approved_at timestamptz
)
language plpgsql
security definer
set search_path to ''
as $fn$
declare
  v_candidate vorquel_knowledge.knowledge_candidates%rowtype;
  v_existing  text;
begin
  select i.knowledge_id into v_existing
  from vorquel_knowledge.knowledge_items i
  where i.candidate_id = p_candidate_id
  limit 1;

  if v_existing is not null then
    return query
    select i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
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
  ) values (p_review_id, p_candidate_id, 'APPROVE', 'HUMAN', p_note);

  update vorquel_knowledge.knowledge_candidates as c
  set status = 'APPROVED'
  where c.candidate_id = p_candidate_id;

  insert into vorquel_knowledge.knowledge_items (
    knowledge_id, candidate_id, source_id, knowledge_type, domain,
    title, summary, epistemic_status, content_hash,
    data_trust_class, instruction_authority,
    scope_type, scope_id, data_classification,
    valid_from
  ) values (
    p_knowledge_id, v_candidate.candidate_id, v_candidate.source_id,
    v_candidate.knowledge_type, v_candidate.domain, v_candidate.title,
    v_candidate.summary, v_candidate.epistemic_status, v_candidate.content_hash,
    'UNTRUSTED_DERIVED', 'NONE',
    v_candidate.scope_type, v_candidate.scope_id, v_candidate.data_classification,
    -- Knowledge is valid from the moment a human approved it, which is now.
    now()
  );

  insert into vorquel_knowledge.knowledge_sources (
    knowledge_source_id, knowledge_id, source_id,
    transcript_id, segment_id, screen_observation_id, start_ms, end_ms,
    locator_kind, locator
  )
  select
    'ksr_' || substr(md5(p_knowledge_id || ':' || ks.knowledge_source_id), 1, 32),
    p_knowledge_id, ks.source_id,
    ks.transcript_id, ks.segment_id, ks.screen_observation_id,
    ks.start_ms, ks.end_ms,
    ks.locator_kind, ks.locator
  from vorquel_knowledge.knowledge_sources ks
  where ks.candidate_id = p_candidate_id;

  return query
  select i.knowledge_id, i.candidate_id, i.source_id, i.knowledge_type,
         i.domain, i.title, i.summary, i.epistemic_status, i.content_hash,
         i.data_trust_class, i.instruction_authority, i.approved_at
  from vorquel_knowledge.knowledge_items i
  where i.knowledge_id = p_knowledge_id;
end
$fn$;

-- ---------------------------------------------------------------------------
-- 6. Batch approval
-- ---------------------------------------------------------------------------
-- Operates only on an explicit list of candidate ids that were already shown to
-- a human, capped so a large batch cannot be waved through in one gesture.

create or replace function public.approve_knowledge_candidates_batch(
  p_candidate_ids text[],
  p_review_prefix text,
  p_note          text default null
)
returns table (candidate_id text, knowledge_id text, approved boolean, detail text)
language plpgsql
security definer
set search_path to ''
as $fn$
declare
  v_id      text;
  v_idx     integer := 0;
  v_knw     text;
  v_review  text;
begin
  if p_candidate_ids is null or array_length(p_candidate_ids, 1) is null then
    raise exception 'no candidates supplied' using errcode = '22023';
  end if;
  if array_length(p_candidate_ids, 1) > 50 then
    raise exception 'batch too large: split it so a human can actually read it'
      using errcode = '22023';
  end if;
  if p_review_prefix !~ '^krv_[a-z0-9][a-z0-9_-]{2,40}$' then
    raise exception 'invalid review prefix' using errcode = '22023';
  end if;

  foreach v_id in array p_candidate_ids loop
    v_idx := v_idx + 1;
    v_knw := 'knw_' || substr(md5(v_id || ':' || p_review_prefix), 1, 32);
    v_review := p_review_prefix || '_' || lpad(v_idx::text, 3, '0');
    begin
      perform public.approve_knowledge_candidate(v_id, v_review, v_knw, p_note);
      return query select v_id, v_knw, true, null::text;
    exception when others then
      -- One bad candidate must not silently drop the rest of the batch.
      return query select v_id, null::text, false, sqlerrm;
    end;
  end loop;
end
$fn$;

-- ---------------------------------------------------------------------------
-- 7. Grants: same least-privilege set as the existing knowledge RPCs.
-- ---------------------------------------------------------------------------

do $grants$
declare
  v_fn text;
begin
  foreach v_fn in array array[
    'public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)',
    'public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)',
    'public.approve_knowledge_candidates_batch(text[],text,text)'
  ] loop
    execute format('revoke all on function %s from public', v_fn);
    execute format('grant execute on function %s to service_role', v_fn);
    if exists (select 1 from pg_roles where rolname = 'watch_runtime') then
      execute format('grant execute on function %s to watch_runtime', v_fn);
    end if;
  end loop;
end
$grants$;

commit;
