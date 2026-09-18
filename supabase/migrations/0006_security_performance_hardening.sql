alter function private.prevent_review_mutation()
  set search_path = pg_catalog, private;

revoke execute on function private.prevent_review_mutation()
  from public, anon, authenticated;

create index transcripts_job_idx
  on public.transcripts (job_id);

create index speaker_turns_source_idx
  on public.speaker_turns (source_id);

create index speaker_turns_job_idx
  on public.speaker_turns (job_id);
