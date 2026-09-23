-- Long-media FAST recovery: checkpointed chunks with atomic segment+checkpoint writes.

alter table public.processing_jobs
  alter column resume_capable set default true;

create unique index if not exists transcripts_job_uq
  on public.transcripts (job_id);

create unique index if not exists processing_runs_one_active_stage_uq
  on public.processing_runs (job_id, stage)
  where status = 'RUNNING';

grant select, insert, update on public.processing_runs to watch_runtime;

create or replace function public.persist_transcription_chunk(
  p_job_id text,
  p_run_id text,
  p_transcript_id text,
  p_segments jsonb,
  p_checkpoint jsonb
)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_source_id text;
  v_expected integer;
  v_touched integer;
begin
  if jsonb_typeof(p_segments) <> 'array' then
    raise exception 'segments must be an array' using errcode = 'check_violation';
  end if;
  v_expected := jsonb_array_length(p_segments);
  if v_expected > 5000 then
    raise exception 'chunk has too many segments' using errcode = 'check_violation';
  end if;

  select source_id into v_source_id
    from public.processing_jobs
   where job_id = p_job_id
     and status = 'RUNNING'
   for update;
  if not found then
    raise exception 'job is not running' using errcode = 'check_violation';
  end if;

  perform 1
    from public.processing_runs
   where run_id = p_run_id
     and job_id = p_job_id
     and stage = 'TRANSCRIBING'
     and status = 'RUNNING'
   for update;
  if not found then
    raise exception 'transcription run is not active' using errcode = 'check_violation';
  end if;

  perform 1
    from public.transcripts
   where transcript_id = p_transcript_id
     and job_id = p_job_id
     and source_id = v_source_id
   for update;
  if not found then
    raise exception 'transcript provenance mismatch' using errcode = 'check_violation';
  end if;

  with incoming as (
    select *
      from jsonb_to_recordset(p_segments) as x(
        segment_id text,
        schema_version text,
        transcript_id text,
        source_id text,
        ordinal integer,
        start_ms bigint,
        end_ms bigint,
        raw_text text,
        effective_text text,
        speaker_id text,
        confidence double precision,
        text_origin text,
        review_status text,
        revision integer,
        words jsonb,
        provenance jsonb,
        data_trust_class text,
        instruction_authority text
      )
  ), valid as (
    select *
      from incoming
     where transcript_id = p_transcript_id
       and source_id = v_source_id
  ), written as (
    insert into public.transcript_segments (
      segment_id, schema_version, transcript_id, source_id, ordinal,
      start_ms, end_ms, raw_text, effective_text, speaker_id, confidence,
      text_origin, review_status, revision, words, provenance,
      data_trust_class, instruction_authority
    )
    select
      segment_id, schema_version, transcript_id, source_id, ordinal,
      start_ms, end_ms, raw_text, effective_text, speaker_id, confidence,
      text_origin, review_status, revision, words, provenance,
      data_trust_class, instruction_authority
      from valid
    on conflict (transcript_id, ordinal) do update
      set segment_id = public.transcript_segments.segment_id
    where public.transcript_segments.segment_id = excluded.segment_id
      and public.transcript_segments.source_id = excluded.source_id
      and public.transcript_segments.start_ms = excluded.start_ms
      and public.transcript_segments.end_ms = excluded.end_ms
      and public.transcript_segments.raw_text = excluded.raw_text
      and public.transcript_segments.effective_text = excluded.effective_text
    returning 1
  )
  select count(*) into v_touched from written;

  if v_touched <> v_expected then
    raise exception 'chunk retry did not match persisted evidence'
      using errcode = 'check_violation';
  end if;

  update public.processing_runs
     set checkpoint = p_checkpoint
   where run_id = p_run_id
     and status = 'RUNNING';

  return v_touched;
end;
$$;

revoke all on function public.persist_transcription_chunk(
  text, text, text, jsonb, jsonb
) from public, anon, authenticated;

grant execute on function public.persist_transcription_chunk(
  text, text, text, jsonb, jsonb
) to service_role, watch_runtime;
