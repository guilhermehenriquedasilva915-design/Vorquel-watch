-- FASE 5 groundwork, and the half of INT-02 that commit 86242fa overstated.
--
-- complete_processing_job is atomic, but the work leading up to it is not:
-- persist_transcription writes the transcript, its segments and the totals as
-- separate round-trips. A worker that dies mid-persist leaves committed rows
-- and a job stuck in RUNNING forever. create_or_reuse_job treats RUNNING as
-- live work and keeps handing that dead job back, so the source can never be
-- processed again.
--
-- Nothing here distinguishes a working worker from a dead one, because nothing
-- records that a worker is still alive. A lease does.

alter table public.processing_jobs
  add column if not exists worker_id text,
  add column if not exists heartbeat_at timestamptz,
  add column if not exists lease_expires_at timestamptz,
  add column if not exists attempt integer not null default 0;

comment on column public.processing_jobs.lease_expires_at is
  'A RUNNING job past this instant has no live worker and may be reclaimed.';

create index if not exists processing_jobs_reclaimable_idx
  on public.processing_jobs (status, lease_expires_at)
  where status = 'RUNNING';

-- ---------------------------------------------------------------------------
-- Claim: take the next queued job, or reclaim one whose worker died.
--
-- FOR UPDATE SKIP LOCKED lets several workers claim concurrently without
-- handing the same job to two of them. Reclaiming keeps status at RUNNING, so
-- the state-machine trigger sees no transition and does not fire.
-- ---------------------------------------------------------------------------

create or replace function public.claim_next_job(
  p_worker_id text,
  p_lease_seconds integer default 120
)
returns public.processing_jobs
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_job public.processing_jobs;
begin
  if coalesce(p_worker_id, '') = '' then
    raise exception 'worker id is required' using errcode = 'check_violation';
  end if;
  if p_lease_seconds is null or p_lease_seconds <= 0 then
    raise exception 'lease must be positive' using errcode = 'check_violation';
  end if;

  select * into v_job
    from public.processing_jobs
   where status = 'QUEUED'
      or (status = 'RUNNING' and lease_expires_at < now())
   order by (status = 'RUNNING') desc, created_at
   for update skip locked
   limit 1;

  if not found then
    return null;
  end if;

  update public.processing_jobs
     set status = 'RUNNING',
         stage = case when stage = 'QUEUED' then 'TRANSCRIBING' else stage end,
         worker_id = p_worker_id,
         attempt = attempt + 1,
         heartbeat_at = now(),
         lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         started_at = coalesce(started_at, now())
   where job_id = v_job.job_id
   returning * into v_job;

  return v_job;
end;
$$;

-- ---------------------------------------------------------------------------
-- Heartbeat: extend the lease, but only for the worker that holds it.
--
-- Returns false when the job was cancelled, finished, or reclaimed by another
-- worker. That is the signal for the caller to stop working on it, which is how
-- a reclaimed worker learns it lost the job.
-- ---------------------------------------------------------------------------

create or replace function public.heartbeat_job(
  p_job_id text,
  p_worker_id text,
  p_lease_seconds integer default 120
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  update public.processing_jobs
     set heartbeat_at = now(),
         lease_expires_at = now() + make_interval(secs => p_lease_seconds)
   where job_id = p_job_id
     and worker_id = p_worker_id
     and status = 'RUNNING';

  return found;
end;
$$;

-- ---------------------------------------------------------------------------
-- Release: drop the lease when a job reaches a terminal state, so a finished
-- job is never mistaken for a reclaimable one.
-- ---------------------------------------------------------------------------

create or replace function private.clear_lease_on_terminal_status()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if new.status in ('SUCCEEDED', 'FAILED', 'CANCELLED')
     and new.status is distinct from old.status then
    new.lease_expires_at := null;
    new.worker_id := null;
  end if;
  return new;
end;
$$;

revoke execute on function private.clear_lease_on_terminal_status()
  from public, anon, authenticated;

drop trigger if exists processing_jobs_clear_lease on public.processing_jobs;

create trigger processing_jobs_clear_lease
before update of status on public.processing_jobs
for each row
execute function private.clear_lease_on_terminal_status();

revoke all on function public.claim_next_job(text, integer)
  from public, anon, authenticated;
revoke all on function public.heartbeat_job(text, text, integer)
  from public, anon, authenticated;

grant execute on function public.claim_next_job(text, integer)
  to service_role, watch_runtime;
grant execute on function public.heartbeat_job(text, text, integer)
  to service_role, watch_runtime;
