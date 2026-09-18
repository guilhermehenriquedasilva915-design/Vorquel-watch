-- Vorquel Watch V1 core persistence.
-- Structured data only. No local filesystem paths or raw media bytes belong here.

create table public.sources (
  source_id text primary key check (left(source_id, 4) = 'src_'),
  schema_version text not null default '1.0',
  source_kind text not null check (source_kind in ('LOCAL_VIDEO', 'LOCAL_AUDIO')),
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  byte_size bigint not null check (byte_size >= 0),
  detected_mime text not null,
  duration_ms bigint not null check (duration_ms >= 0),
  ingest_status text not null check (ingest_status in ('READY', 'REJECTED', 'DELETED')),
  container text,
  has_video boolean,
  has_audio boolean,
  video_stream_count integer check (video_stream_count is null or video_stream_count >= 0),
  audio_stream_count integer check (audio_stream_count is null or audio_stream_count >= 0),
  retained_until timestamptz,
  latest_transcript_id text,
  latest_successful_job_id text,
  external_metadata jsonb not null default '{}'::jsonb,
  security_policy_version text not null default 'source-guard/1',
  data_trust_class text not null default 'UNTRUSTED_MEDIA'
    check (data_trust_class = 'UNTRUSTED_MEDIA'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now(),
  unique (content_sha256)
);

create table public.processing_jobs (
  job_id text primary key check (left(job_id, 4) = 'job_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  mode text not null check (mode in ('FAST', 'STANDARD', 'SPEAKERS')),
  status text not null check (
    status in ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')
  ),
  stage text not null check (
    stage in (
      'QUEUED','PROBING','PREPARING_AUDIO','TRANSCRIBING','ALIGNING',
      'DIARIZING','MERGING','INDEXING','FINALIZING','COMPLETE'
    )
  ),
  progress_permille integer not null default 0
    check (progress_permille between 0 and 1000),
  pipeline_version text not null,
  config_hash text not null check (config_hash ~ '^[0-9a-f]{64}$'),
  language_hint text,
  resume_capable boolean not null default true,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  check (completed_at is null or started_at is null or completed_at >= started_at)
);

create table public.processing_runs (
  run_id text primary key check (left(run_id, 4) = 'run_'),
  job_id text not null references public.processing_jobs(job_id) on delete cascade,
  stage text not null,
  attempt integer not null default 1 check (attempt > 0),
  status text not null check (status in ('RUNNING','SUCCEEDED','FAILED','CANCELLED')),
  checkpoint jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  check (completed_at is null or completed_at >= started_at)
);

create table public.transcripts (
  transcript_id text primary key check (left(transcript_id, 4) = 'trn_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  job_id text not null references public.processing_jobs(job_id) on delete restrict,
  language text,
  text_source text not null check (text_source in ('CAPTION','ASR')),
  segment_count integer not null default 0 check (segment_count >= 0),
  word_count integer not null default 0 check (word_count >= 0),
  duration_ms bigint not null check (duration_ms >= 0),
  alignment text not null check (alignment in ('NONE','WHISPERX')),
  diarization text not null check (diarization in ('NONE','COMPLETED')),
  engine jsonb not null default '{}'::jsonb,
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now()
);

create table public.speakers (
  speaker_id text primary key check (left(speaker_id, 4) = 'spk_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  label text not null,
  display_name text,
  name_source text not null default 'NONE'
    check (name_source in ('NONE','USER_ASSIGNED')),
  review_status text not null default 'UNREVIEWED'
    check (review_status in ('UNREVIEWED','HUMAN_CONFIRMED','HUMAN_CORRECTED')),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class in ('UNTRUSTED_DERIVED','USER_AUTHORED_DATA')),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now()
);

create table public.transcript_segments (
  segment_id text primary key check (left(segment_id, 4) = 'seg_'),
  schema_version text not null default '1.0',
  transcript_id text not null references public.transcripts(transcript_id) on delete cascade,
  source_id text not null references public.sources(source_id) on delete restrict,
  ordinal integer not null check (ordinal >= 0),
  start_ms bigint not null check (start_ms >= 0),
  end_ms bigint not null check (end_ms >= start_ms),
  raw_text text not null,
  effective_text text not null,
  speaker_id text references public.speakers(speaker_id) on delete set null,
  confidence double precision check (
    confidence is null or (confidence >= 0.0 and confidence <= 1.0)
  ),
  text_origin text not null check (text_origin in ('CAPTION','ASR','HUMAN_CORRECTED')),
  review_status text not null default 'UNREVIEWED'
    check (review_status in ('UNREVIEWED','HUMAN_CONFIRMED','HUMAN_CORRECTED','HUMAN_REJECTED')),
  revision integer not null default 1 check (revision > 0),
  words jsonb,
  provenance jsonb not null default '{}'::jsonb,
  data_trust_class text not null
    check (data_trust_class in ('UNTRUSTED_MEDIA','UNTRUSTED_DERIVED','USER_AUTHORED_DATA')),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now()
);

create table public.speaker_turns (
  turn_id text primary key check (left(turn_id, 5) = 'turn_'),
  schema_version text not null default '1.0',
  speaker_id text not null references public.speakers(speaker_id) on delete cascade,
  source_id text not null references public.sources(source_id) on delete restrict,
  job_id text not null references public.processing_jobs(job_id) on delete restrict,
  start_ms bigint not null check (start_ms >= 0),
  end_ms bigint not null check (end_ms >= start_ms),
  confidence double precision check (
    confidence is null or (confidence >= 0.0 and confidence <= 1.0)
  ),
  created_at timestamptz not null default now()
);

create table public.reviews (
  review_id text primary key check (left(review_id, 4) = 'rev_'),
  schema_version text not null default '1.0',
  target_type text not null check (target_type in ('SEGMENT','SPEAKER')),
  target_id text not null,
  action text not null check (action in ('CONFIRM','CORRECT_TEXT','REJECT','RENAME_SPEAKER')),
  actor_type text not null default 'HUMAN' check (actor_type = 'HUMAN'),
  before_hash text not null check (before_hash ~ '^[0-9a-f]{64}$'),
  after_hash text not null check (after_hash ~ '^[0-9a-f]{64}$'),
  patch jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table public.artifacts (
  artifact_id text primary key check (left(artifact_id, 4) = 'art_'),
  schema_version text not null default '1.0',
  source_id text not null references public.sources(source_id) on delete restrict,
  job_id text not null references public.processing_jobs(job_id) on delete restrict,
  artifact_type text not null check (
    artifact_type in ('TRANSCRIPT_JSON','TRANSCRIPT_TXT','SRT','VTT','MARKDOWN','NORMALIZED_AUDIO','CAPTION_RAW')
  ),
  mime text not null,
  byte_size bigint not null check (byte_size >= 0),
  artifact_sha256 text not null check (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  retention_class text not null check (retention_class in ('TEMP','DERIVED','USER_EXPORT')),
  data_trust_class text not null default 'UNTRUSTED_DERIVED'
    check (data_trust_class = 'UNTRUSTED_DERIVED'),
  instruction_authority text not null default 'NONE'
    check (instruction_authority = 'NONE'),
  created_at timestamptz not null default now()
);
