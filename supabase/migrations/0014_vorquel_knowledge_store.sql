-- Vorquel Knowledge Store V0.1
--
-- Purpose:
-- Persist reviewed learning extracted from Watch sources without promoting
-- media-derived content directly into canonical company truth.
--
-- Trust invariant:
-- candidate/item text derived from media remains data-only forever.
-- Human approval authorizes persistence/use as a reference; it does NOT grant
-- instruction authority.

create schema if not exists vorquel_knowledge;

revoke all on schema vorquel_knowledge from public, anon, authenticated;

create table vorquel_knowledge.knowledge_candidates (
  candidate_id text primary key check (left(candidate_id, 4) = 'knd_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  knowledge_type text not null
    check (knowledge_type in (
      'CONCEPT','PROCEDURE','PATTERN','ANTI_PATTERN','ERROR','FIX',
      'EXAMPLE','CLAIM','TOOL_USAGE','CHECKLIST','WARNING'
    )),
  domain text not null check (char_length(domain) between 1 and 120),
  title text not null check (char_length(title) between 1 and 300),
  summary text not null check (char_length(summary) between 1 and 12000),
  epistemic_status text not null
    check (epistemic_status in (
      'OBSERVADO','DECLARADO','MEDIDO','INFERIDO','HIPOTESE',
      'ESTIMADO','DESCONHECIDO','CONFLITANTE','INVALIDADO'
    )),
  status text not null default 'PENDING'
    check (status in ('PENDING','APPROVED','REJECTED')),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now(),
  unique (source_id, content_hash)
);

create table vorquel_knowledge.knowledge_reviews (
  knowledge_review_id text primary key check (left(knowledge_review_id, 4) = 'krv_'),
  candidate_id text not null
    references vorquel_knowledge.knowledge_candidates(candidate_id)
    on delete restrict,
  action text not null check (action in ('APPROVE','REJECT')),
  actor_type text not null default 'HUMAN' check (actor_type = 'HUMAN'),
  note text,
  created_at timestamptz not null default now()
);

create table vorquel_knowledge.knowledge_items (
  knowledge_id text primary key check (left(knowledge_id, 4) = 'knw_'),
  schema_version text not null default '1.0',
  candidate_id text not null unique
    references vorquel_knowledge.knowledge_candidates(candidate_id)
    on delete restrict,
  source_id text not null references public.sources(source_id) on delete restrict,
  knowledge_type text not null
    check (knowledge_type in (
      'CONCEPT','PROCEDURE','PATTERN','ANTI_PATTERN','ERROR','FIX',
      'EXAMPLE','CLAIM','TOOL_USAGE','CHECKLIST','WARNING'
    )),
  domain text not null check (char_length(domain) between 1 and 120),
  title text not null check (char_length(title) between 1 and 300),
  summary text not null check (char_length(summary) between 1 and 12000),
  epistemic_status text not null
    check (epistemic_status in (
      'OBSERVADO','DECLARADO','MEDIDO','INFERIDO','HIPOTESE',
      'ESTIMADO','DESCONHECIDO','CONFLITANTE','INVALIDADO'
    )),
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  approved_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table vorquel_knowledge.knowledge_sources (
  knowledge_source_id text primary key check (left(knowledge_source_id, 4) = 'ksr_'),
  candidate_id text
    references vorquel_knowledge.knowledge_candidates(candidate_id)
    on delete restrict,
  knowledge_id text
    references vorquel_knowledge.knowledge_items(knowledge_id)
    on delete restrict,
  source_id text not null references public.sources(source_id) on delete restrict,
  transcript_id text references public.transcripts(transcript_id) on delete restrict,
  segment_id text references public.transcript_segments(segment_id) on delete restrict,
  screen_observation_id text
    references public.screen_observations(observation_id) on delete restrict,
  start_ms bigint check (start_ms is null or start_ms >= 0),
  end_ms bigint check (end_ms is null or end_ms >= 0),
  created_at timestamptz not null default now(),
  check (candidate_id is not null or knowledge_id is not null),
  check (end_ms is null or start_ms is null or end_ms >= start_ms)
);

-- Search only approved knowledge items. No vectors/RAG in V0.1.
alter table vorquel_knowledge.knowledge_items
  add column search_vector tsvector
  generated always as (
    to_tsvector(
      'simple',
      coalesce(title, '') || ' ' ||
      coalesce(summary, '') || ' ' ||
      coalesce(domain, '')
    )
  ) stored;

create index knowledge_items_search_vector_gin
  on vorquel_knowledge.knowledge_items using gin (search_vector);

create index knowledge_candidates_source_idx
  on vorquel_knowledge.knowledge_candidates (source_id, created_at desc);

create index knowledge_candidates_status_idx
  on vorquel_knowledge.knowledge_candidates (status, created_at desc);

create index knowledge_sources_candidate_idx
  on vorquel_knowledge.knowledge_sources (candidate_id);

create index knowledge_sources_item_idx
  on vorquel_knowledge.knowledge_sources (knowledge_id);

create index knowledge_sources_source_time_idx
  on vorquel_knowledge.knowledge_sources (source_id, start_ms, end_ms);

-- No direct client access in V0.1.
alter table vorquel_knowledge.knowledge_candidates enable row level security;
alter table vorquel_knowledge.knowledge_reviews enable row level security;
alter table vorquel_knowledge.knowledge_items enable row level security;
alter table vorquel_knowledge.knowledge_sources enable row level security;

revoke all on all tables in schema vorquel_knowledge
  from public, anon, authenticated;

comment on schema vorquel_knowledge is
  'Reviewed, provenance-preserving learning for the Vorquel Brain. External content never has instruction authority.';

comment on table vorquel_knowledge.knowledge_candidates is
  'Untrusted derived learning proposals awaiting explicit human approval.';

comment on table vorquel_knowledge.knowledge_items is
  'Human-approved persistent references. Approval does not change instruction authority.';
