-- DB-01: a dedicated least-privilege role for the Watch runtime.
--
-- The backend currently authenticates as service_role, which holds DELETE,
-- TRUNCATE, TRIGGER and REFERENCES on all nine tables. Nothing in the runtime
-- needs any of those. A bug or a compromised local process could therefore
-- destroy evidence that the product exists to preserve.
--
-- watch_runtime is granted exactly what the code paths in db.py use today.
-- Anything else is withheld deliberately and listed at the bottom of this file
-- so adding a privilege stays a conscious act.
--
-- BYPASSRLS is required, not a shortcut: the domain tables have RLS enabled
-- with no policies (deny-all by design, since anon and authenticated have no
-- direct access in V1). Without it every statement would be denied. The real
-- privilege boundary here is the table grant list, exactly as it is for
-- service_role - but this role cannot delete or truncate anything.
--
-- NOTE: granting the role is only half of the change. PostgREST switches into
-- the role named by the JWT "role" claim, so the runtime must present a token
-- minted with "role": "watch_runtime" and signed with the project JWT secret.
-- That secret is not in this repository and must never be. Until such a token
-- is configured, the runtime keeps using service_role and this role is
-- provisioned but unused.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'watch_runtime') then
    create role watch_runtime nologin noinherit bypassrls;
  end if;
end
$$;

-- PostgREST's authenticator must be able to SET ROLE into it.
grant watch_runtime to authenticator;

grant usage on schema public to watch_runtime;

-- sources: read and register. The latest_* pointers are written by
-- complete_processing_job, which is SECURITY DEFINER, so no UPDATE is needed.
grant select, insert on public.sources to watch_runtime;

-- processing_jobs: claim_job, update_job, cancel_job and set_terminal_status
-- all update job rows.
grant select, insert, update on public.processing_jobs to watch_runtime;

-- transcripts: created by the worker, then finish_transcript writes the totals.
grant select, insert, update on public.transcripts to watch_runtime;

-- transcript_segments: written once, never edited in place. Human corrections
-- arrive as review rows plus an effective_text update, which V1 performs
-- through the control plane, not the worker.
grant select, insert on public.transcript_segments to watch_runtime;

-- speakers and speaker_turns: read-only until SPEAKERS mode exists.
grant select on public.speakers to watch_runtime;
grant select on public.speaker_turns to watch_runtime;

-- artifacts: exports are created and listed.
grant select, insert on public.artifacts to watch_runtime;

grant execute on function public.search_transcript_segments(
  text, text[], bigint, bigint, integer
) to watch_runtime;

grant execute on function public.complete_processing_job(text, text)
  to watch_runtime;

-- Deliberately withheld, to be granted only when a code path needs them:
--
--   DELETE, TRUNCATE, TRIGGER, REFERENCES   on every table
--   UPDATE                                  on sources, transcript_segments,
--                                           speakers, speaker_turns, artifacts
--   INSERT/UPDATE                           on processing_runs (FASE 5 resume)
--   INSERT                                  on speakers, speaker_turns
--                                           (FASE 7 diarization)
--   INSERT                                  on reviews (control-plane review UI)
--
-- Retention and deletion are maintenance operations and must not run with the
-- runtime's role. They stay with an administrative connection.
