-- 0021: scope isolation, data classification, generic provenance, compatibility envelope
--
-- Additive only. No UPDATE/DELETE path is introduced for existing knowledge rows
-- beyond the one-time backfill of locators, which only re-expresses media facts
-- already stored in dedicated columns.
--
-- Trust boundary unchanged: every row here stays UNTRUSTED_DERIVED with
-- instruction_authority = NONE. Nothing in this migration grants authority.

begin;

-- ---------------------------------------------------------------------------
-- 1. Scope isolation
-- ---------------------------------------------------------------------------
-- Existing rows predate scoping. They were produced by the operator for Vorquel
-- itself, so GLOBAL_VORQUEL is the only defensible default; anything narrower
-- would claim a client association that was never recorded.

alter table vorquel_knowledge.knowledge_candidates
  add column if not exists scope_type text not null default 'GLOBAL_VORQUEL',
  add column if not exists scope_id   text not null default 'GLOBAL';

alter table vorquel_knowledge.knowledge_items
  add column if not exists scope_type text not null default 'GLOBAL_VORQUEL',
  add column if not exists scope_id   text not null default 'GLOBAL';

do $mig$
begin
  if not exists (select 1 from pg_constraint where conname = 'knowledge_candidates_scope_type_check') then
    alter table vorquel_knowledge.knowledge_candidates
      add constraint knowledge_candidates_scope_type_check
      check (scope_type in ('GLOBAL_VORQUEL','CLIENT','PROJECT','PRIVATE_TEST'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_candidates_scope_id_check') then
    alter table vorquel_knowledge.knowledge_candidates
      add constraint knowledge_candidates_scope_id_check
      check (scope_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$');
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_candidates_scope_shape_check') then
    alter table vorquel_knowledge.knowledge_candidates
      add constraint knowledge_candidates_scope_shape_check
      check ((scope_type = 'GLOBAL_VORQUEL') = (scope_id = 'GLOBAL'));
  end if;

  if not exists (select 1 from pg_constraint where conname = 'knowledge_items_scope_type_check') then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_scope_type_check
      check (scope_type in ('GLOBAL_VORQUEL','CLIENT','PROJECT','PRIVATE_TEST'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_items_scope_id_check') then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_scope_id_check
      check (scope_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$');
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_items_scope_shape_check') then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_scope_shape_check
      check ((scope_type = 'GLOBAL_VORQUEL') = (scope_id = 'GLOBAL'));
  end if;
end
$mig$;

create index if not exists knowledge_items_scope_idx
  on vorquel_knowledge.knowledge_items (scope_type, scope_id, lifecycle_state);

-- ---------------------------------------------------------------------------
-- 2. Data classification
-- ---------------------------------------------------------------------------
-- SECRET is deliberately absent from the allowed set: a secret must never be
-- representable as knowledge, so the constraint fails closed instead of
-- offering a label for it.

alter table vorquel_knowledge.knowledge_candidates
  add column if not exists data_classification text not null default 'INTERNAL';
alter table vorquel_knowledge.knowledge_items
  add column if not exists data_classification text not null default 'INTERNAL';

do $mig$
begin
  if not exists (select 1 from pg_constraint where conname = 'knowledge_candidates_data_classification_check') then
    alter table vorquel_knowledge.knowledge_candidates
      add constraint knowledge_candidates_data_classification_check
      check (data_classification in ('PUBLIC','INTERNAL','CLIENT_CONFIDENTIAL','PII'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_items_data_classification_check') then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_data_classification_check
      check (data_classification in ('PUBLIC','INTERNAL','CLIENT_CONFIDENTIAL','PII'));
  end if;
end
$mig$;

-- ---------------------------------------------------------------------------
-- 3. Compatibility envelope
-- ---------------------------------------------------------------------------

alter table vorquel_knowledge.knowledge_items
  add column if not exists observed_n8n_version  text,
  add column if not exists node_type             text,
  add column if not exists node_version          text,
  add column if not exists integration_version   text,
  add column if not exists observed_at           timestamptz,
  add column if not exists last_verified_at      timestamptz,
  add column if not exists compatibility_status  text not null default 'UNKNOWN_COMPATIBILITY';

do $mig$
begin
  if not exists (select 1 from pg_constraint where conname = 'knowledge_items_compatibility_status_check') then
    alter table vorquel_knowledge.knowledge_items
      add constraint knowledge_items_compatibility_status_check
      check (compatibility_status in ('CURRENT','STALE','UNKNOWN_COMPATIBILITY','DEPRECATED'));
  end if;
end
$mig$;

-- ---------------------------------------------------------------------------
-- 4. Generic provenance locator
-- ---------------------------------------------------------------------------
-- source_id stays the root. The media-specific columns are kept so existing
-- readers keep working; new source kinds use locator_kind + locator.

alter table vorquel_knowledge.knowledge_sources
  add column if not exists locator_kind text not null default 'MEDIA_TIME',
  add column if not exists locator      jsonb not null default '{}'::jsonb;

do $mig$
begin
  if not exists (select 1 from pg_constraint where conname = 'knowledge_sources_locator_kind_check') then
    alter table vorquel_knowledge.knowledge_sources
      add constraint knowledge_sources_locator_kind_check
      check (locator_kind in (
        'MEDIA_TIME','PDF_PAGE','DOC_SECTION','REPO_FILE','GIT_COMMIT',
        'WORKFLOW_NODE','N8N_EXECUTION','MESSAGE','GENERIC'));
  end if;
  -- A locator is an address, never a payload and never a command.
  if not exists (select 1 from pg_constraint where conname = 'knowledge_sources_locator_shape_check') then
    alter table vorquel_knowledge.knowledge_sources
      add constraint knowledge_sources_locator_shape_check
      check (jsonb_typeof(locator) = 'object' and pg_column_size(locator) <= 2048);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'knowledge_sources_locator_no_secret_check') then
    alter table vorquel_knowledge.knowledge_sources
      add constraint knowledge_sources_locator_no_secret_check
      check (not (locator ?| array[
        'secret','token','password','api_key','apiKey','authorization',
        'credential','credentials','command','shell','sql','query','payload','body']));
  end if;
end
$mig$;

-- One-time backfill: express the media locator already stored in the dedicated
-- columns using the generic shape, without inventing any new fact.
update vorquel_knowledge.knowledge_sources
set locator = jsonb_strip_nulls(jsonb_build_object(
      'transcript_id', transcript_id,
      'segment_id', segment_id,
      'screen_observation_id', screen_observation_id,
      'start_ms', start_ms,
      'end_ms', end_ms))
where locator = '{}'::jsonb;

create index if not exists knowledge_sources_locator_kind_idx
  on vorquel_knowledge.knowledge_sources (locator_kind);

commit;
