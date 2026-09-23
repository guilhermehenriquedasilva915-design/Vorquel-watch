-- INT-03: enforce the job state machine in the database.
-- INT-02: make job completion a single atomic operation.
--
-- Previously the worker wrote status transitions with unconditional updates
-- and finished a job with three separate statements (transcript totals, source
-- pointers, job status). A job cancelled mid-run could therefore be overwritten
-- with SUCCEEDED, and a failure between statements left a half-completed job.

-- ---------------------------------------------------------------------------
-- INT-03 - state machine
--
-- Allowed:   QUEUED  -> RUNNING | CANCELLED
--            RUNNING -> SUCCEEDED | FAILED | CANCELLED
-- Terminal:  SUCCEEDED, FAILED, CANCELLED never transition again.
--
-- Updates that do not change status (progress, stage, error detail) pass
-- through untouched.
-- ---------------------------------------------------------------------------

create or replace function private.enforce_job_status_transition()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if new.status = old.status then
    return new;
  end if;

  if old.status in ('SUCCEEDED', 'FAILED', 'CANCELLED') then
    raise exception
      'job % is terminal (%) and cannot transition to %',
      old.job_id, old.status, new.status
      using errcode = 'check_violation';
  end if;

  if old.status = 'QUEUED' and new.status not in ('RUNNING', 'CANCELLED') then
    raise exception
      'illegal job transition QUEUED -> % for job %', new.status, old.job_id
      using errcode = 'check_violation';
  end if;

  if old.status = 'RUNNING'
     and new.status not in ('SUCCEEDED', 'FAILED', 'CANCELLED') then
    raise exception
      'illegal job transition RUNNING -> % for job %', new.status, old.job_id
      using errcode = 'check_violation';
  end if;

  return new;
end;
$$;

revoke execute on function private.enforce_job_status_transition()
  from public, anon, authenticated;

drop trigger if exists processing_jobs_status_transition on public.processing_jobs;

create trigger processing_jobs_status_transition
before update of status on public.processing_jobs
for each row
execute function private.enforce_job_status_transition();

-- ---------------------------------------------------------------------------
-- INT-02 - atomic completion
--
-- Locks the job row, revalidates every precondition, then writes the source
-- pointers and the terminal job status in one transaction. Any failure rolls
-- the whole thing back, so a job is never left partially completed.
--
-- The FOR UPDATE lock plus the status = 'RUNNING' predicate on the final
-- update is what closes the cancel race: a concurrent cancellation either
-- blocks on the lock and then fails this precondition, or lands first and
-- makes the completion fail outright.
-- ---------------------------------------------------------------------------

create or replace function public.complete_processing_job(
  p_job_id text,
  p_transcript_id text
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_job public.processing_jobs;
  v_transcript public.transcripts;
  v_segment_count integer;
begin
  select * into v_job
    from public.processing_jobs
   where job_id = p_job_id
     for update;

  if not found then
    raise exception 'job not found' using errcode = 'no_data_found';
  end if;

  if v_job.status <> 'RUNNING' then
    raise exception 'job is %, expected RUNNING', v_job.status
      using errcode = 'check_violation';
  end if;

  select * into v_transcript
    from public.transcripts
   where transcript_id = p_transcript_id
     for update;

  if not found then
    raise exception 'transcript not found' using errcode = 'no_data_found';
  end if;

  if v_transcript.job_id <> p_job_id
     or v_transcript.source_id <> v_job.source_id then
    raise exception 'transcript does not belong to this job'
      using errcode = 'check_violation';
  end if;

  select count(*) into v_segment_count
    from public.transcript_segments
   where transcript_id = p_transcript_id;

  if v_segment_count <> v_transcript.segment_count then
    raise exception 'transcript segment_count does not match stored segments'
      using errcode = 'check_violation';
  end if;

  update public.sources
     set latest_transcript_id = p_transcript_id,
         latest_successful_job_id = p_job_id
   where source_id = v_job.source_id;

  update public.processing_jobs
     set status = 'SUCCEEDED',
         stage = 'COMPLETE',
         progress_permille = 1000,
         completed_at = now(),
         error_code = null,
         error_message = null
   where job_id = p_job_id
     and status = 'RUNNING';

  if not found then
    raise exception 'job status changed during completion'
      using errcode = 'serialization_failure';
  end if;
end;
$$;

revoke all on function public.complete_processing_job(text, text)
  from public, anon, authenticated;

grant execute on function public.complete_processing_job(text, text)
  to service_role;
