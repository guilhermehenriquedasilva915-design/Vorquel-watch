-- INT-01: make cross-source provenance impossible at the database layer.
--
-- Every relationship previously used independent single-column foreign keys,
-- so the database accepted a transcript pointing at source B while its job
-- pointed at source A. Python-side checks are not sufficient: the constraint
-- has to live in the schema.
--
-- The fix ties each child row to the same source as its parent using composite
-- foreign keys. The existing single-column source_id foreign keys are kept so
-- ON DELETE RESTRICT against sources still applies.

-- ---------------------------------------------------------------------------
-- Unique constraints required as composite foreign key targets.
-- Each is redundant with the primary key by itself, but PostgreSQL requires a
-- unique constraint covering exactly the referenced column list.
-- ---------------------------------------------------------------------------

alter table public.processing_jobs
  add constraint processing_jobs_job_source_uq unique (job_id, source_id);

alter table public.transcripts
  add constraint transcripts_transcript_source_uq unique (transcript_id, source_id);

alter table public.speakers
  add constraint speakers_speaker_source_uq unique (speaker_id, source_id);

-- ---------------------------------------------------------------------------
-- transcript.source_id must equal its job's source_id
-- ---------------------------------------------------------------------------

alter table public.transcripts
  drop constraint transcripts_job_id_fkey,
  add constraint transcripts_job_source_fkey
    foreign key (job_id, source_id)
    references public.processing_jobs (job_id, source_id)
    on delete restrict;

-- ---------------------------------------------------------------------------
-- segment.source_id must equal its transcript's source_id, and a segment's
-- speaker must belong to that same source.
--
-- ON DELETE SET NULL names only speaker_id: source_id is NOT NULL and must not
-- be nulled when a speaker row disappears.
-- ---------------------------------------------------------------------------

alter table public.transcript_segments
  drop constraint transcript_segments_transcript_id_fkey,
  add constraint transcript_segments_transcript_source_fkey
    foreign key (transcript_id, source_id)
    references public.transcripts (transcript_id, source_id)
    on delete cascade;

alter table public.transcript_segments
  drop constraint transcript_segments_speaker_id_fkey,
  add constraint transcript_segments_speaker_source_fkey
    foreign key (speaker_id, source_id)
    references public.speakers (speaker_id, source_id)
    on delete set null (speaker_id);

-- ---------------------------------------------------------------------------
-- speaker_turn.source_id must equal both its speaker's and its job's source_id
-- ---------------------------------------------------------------------------

alter table public.speaker_turns
  drop constraint speaker_turns_speaker_id_fkey,
  add constraint speaker_turns_speaker_source_fkey
    foreign key (speaker_id, source_id)
    references public.speakers (speaker_id, source_id)
    on delete cascade;

alter table public.speaker_turns
  drop constraint speaker_turns_job_id_fkey,
  add constraint speaker_turns_job_source_fkey
    foreign key (job_id, source_id)
    references public.processing_jobs (job_id, source_id)
    on delete restrict;

-- ---------------------------------------------------------------------------
-- artifact.source_id must equal its job's source_id
-- ---------------------------------------------------------------------------

alter table public.artifacts
  drop constraint artifacts_job_id_fkey,
  add constraint artifacts_job_source_fkey
    foreign key (job_id, source_id)
    references public.processing_jobs (job_id, source_id)
    on delete restrict;

-- ---------------------------------------------------------------------------
-- The denormalized pointers on sources had no foreign key at all and could
-- reference a transcript or job belonging to a different source.
--
-- These use MATCH SIMPLE (the default): while the pointer is NULL the
-- constraint is not enforced, which is what allows a source row to be inserted
-- before any transcript or job exists.
-- ---------------------------------------------------------------------------

alter table public.sources
  add constraint sources_latest_transcript_fkey
    foreign key (latest_transcript_id, source_id)
    references public.transcripts (transcript_id, source_id)
    on delete set null (latest_transcript_id);

alter table public.sources
  add constraint sources_latest_job_fkey
    foreign key (latest_successful_job_id, source_id)
    references public.processing_jobs (job_id, source_id)
    on delete set null (latest_successful_job_id);
