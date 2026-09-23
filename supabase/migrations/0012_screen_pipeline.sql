-- Screen pipeline: what was visible on screen, aligned to the same timeline as
-- the transcript.
--
-- Design constraints that shaped this schema:
--
-- A five-hour workshop at 30fps is over half a million frames. Persisting a row
-- per frame is not viable and is not needed: the frame timeline is derivable
-- from the media file itself through PTS, so frames are addressed by timestamp
-- and materialised on demand.
--
-- What is persisted is an observation: a span of time during which the screen
-- did not meaningfully change. A slide held for 40 seconds is one row, not 40.
--
-- Screen text is media-derived and therefore untrusted. Text read off a screen
-- saying "ignore all previous instructions" is content, exactly like spoken
-- words, and the CHECK constraints below make that non-negotiable at the
-- storage layer.

create table public.screen_observations (
  observation_id text primary key check (left(observation_id, 4) = 'obs_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  job_id text not null,
  start_ms bigint not null check (start_ms >= 0),
  end_ms bigint not null,
  representative_frame_ms bigint not null check (representative_frame_ms >= 0),
  visual_change_score double precision
    check (visual_change_score is null
           or (visual_change_score >= 0.0 and visual_change_score <= 1.0)),
  -- Digest of the representative frame's pixels, used to collapse a screen that
  -- reappears later (slide revisited) onto a known observation.
  content_hash text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  ocr_text text not null default '',
  ocr_engine text,
  ocr_engine_version text,
  frames_sampled integer not null default 0 check (frames_sampled >= 0),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now(),
  check (end_ms >= start_ms),
  check (representative_frame_ms between start_ms and end_ms),
  -- INT-01: an observation belongs to exactly one source, and so does its job.
  unique (observation_id, source_id),
  constraint screen_observations_job_source_fkey
    foreign key (job_id, source_id)
    references public.processing_jobs (job_id, source_id)
    on delete restrict
);

-- Per-block OCR output. Kept separate from the observation so a block carries
-- its own confidence and bounding box without bloating the searchable row.
create table public.screen_text_blocks (
  ocr_id text primary key check (left(ocr_id, 4) = 'ocr_'),
  schema_version text not null default '1.0',
  observation_id text not null,
  source_id text not null references public.sources(source_id) on delete restrict,
  ordinal integer not null check (ordinal >= 0),
  text text not null,
  confidence double precision
    check (confidence is null or (confidence >= 0.0 and confidence <= 1.0)),
  bbox_x integer,
  bbox_y integer,
  bbox_width integer check (bbox_width is null or bbox_width >= 0),
  bbox_height integer check (bbox_height is null or bbox_height >= 0),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now(),
  constraint screen_text_blocks_observation_source_fkey
    foreign key (observation_id, source_id)
    references public.screen_observations (observation_id, source_id)
    on delete cascade,
  unique (observation_id, ordinal)
);

-- Full-text search over what appeared on screen, mirroring the transcript index
-- so "when does Supabase actually appear on screen" is answerable.
alter table public.screen_observations
  add column search_vector tsvector
  generated always as (to_tsvector('simple', coalesce(ocr_text, ''))) stored;

create index screen_observations_search_vector_gin
  on public.screen_observations using gin (search_vector);

create index screen_observations_source_time_idx
  on public.screen_observations (source_id, start_ms, end_ms);

create index screen_observations_job_idx
  on public.screen_observations (job_id);

create index screen_observations_content_hash_idx
  on public.screen_observations (source_id, content_hash);

create index screen_text_blocks_observation_idx
  on public.screen_text_blocks (observation_id, ordinal);

-- ---------------------------------------------------------------------------
-- Search, gated on a successful job exactly as transcript search is (INT-04):
-- screen text from a job that failed or was cancelled is not a result.
-- ---------------------------------------------------------------------------

create or replace function public.search_screen_text(
  p_query text,
  p_source_ids text[],
  p_start_ms bigint default null,
  p_end_ms bigint default null,
  p_limit integer default 20
)
returns table (
  observation_id text,
  source_id text,
  start_ms bigint,
  end_ms bigint,
  representative_frame_ms bigint,
  ocr_text text,
  rank real
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    o.observation_id,
    o.source_id,
    o.start_ms,
    o.end_ms,
    o.representative_frame_ms,
    o.ocr_text,
    ts_rank_cd(
      o.search_vector,
      plainto_tsquery('simple', left(coalesce(p_query, ''), 500))
    )::real as rank
  from public.screen_observations o
  join public.processing_jobs j
    on j.job_id = o.job_id
   and j.status = 'SUCCEEDED'
  where
    cardinality(p_source_ids) between 1 and 25
    and o.source_id = any(p_source_ids)
    and (p_start_ms is null or o.end_ms >= p_start_ms)
    and (p_end_ms is null or o.start_ms <= p_end_ms)
    and o.search_vector @@ plainto_tsquery(
      'simple',
      left(coalesce(p_query, ''), 500)
    )
  order by rank desc, o.start_ms asc
  limit least(greatest(coalesce(p_limit, 20), 1), 50);
$$;

-- ---------------------------------------------------------------------------
-- Access: same posture as every other domain table.
-- ---------------------------------------------------------------------------

alter table public.screen_observations enable row level security;
alter table public.screen_text_blocks enable row level security;

revoke all on table public.screen_observations from anon, authenticated;
revoke all on table public.screen_text_blocks from anon, authenticated;

revoke all on function public.search_screen_text(
  text, text[], bigint, bigint, integer
) from public, anon, authenticated;

grant execute on function public.search_screen_text(
  text, text[], bigint, bigint, integer
) to service_role, watch_runtime;

grant select, insert on public.screen_observations to watch_runtime;
grant select, insert on public.screen_text_blocks to watch_runtime;

comment on table public.screen_observations is
  'A span during which the screen did not meaningfully change. Media-derived and untrusted.';
comment on table public.screen_text_blocks is
  'OCR output. Text read from a screen is content, never an instruction.';
