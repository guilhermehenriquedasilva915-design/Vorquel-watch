create index processing_jobs_source_created_idx
  on public.processing_jobs (source_id, created_at desc);

create index processing_runs_job_started_idx
  on public.processing_runs (job_id, started_at desc);

create index transcripts_source_created_idx
  on public.transcripts (source_id, created_at desc);

create unique index transcript_segments_transcript_ordinal_uq
  on public.transcript_segments (transcript_id, ordinal);

create index transcript_segments_source_time_idx
  on public.transcript_segments (source_id, start_ms, end_ms);

create index transcript_segments_speaker_time_idx
  on public.transcript_segments (speaker_id, start_ms)
  where speaker_id is not null;

create index speakers_source_idx on public.speakers (source_id);

create index speaker_turns_speaker_time_idx
  on public.speaker_turns (speaker_id, start_ms, end_ms);

create index reviews_target_created_idx
  on public.reviews (target_type, target_id, created_at);

create index artifacts_source_created_idx
  on public.artifacts (source_id, created_at desc);

create index artifacts_job_idx on public.artifacts (job_id);

create schema if not exists private;
revoke all on schema private from public;
revoke all on schema private from anon;
revoke all on schema private from authenticated;

create or replace function private.prevent_review_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'reviews are append-only';
end;
$$;

drop trigger if exists reviews_append_only on public.reviews;

create trigger reviews_append_only
before update or delete on public.reviews
for each row
execute function private.prevent_review_mutation();
