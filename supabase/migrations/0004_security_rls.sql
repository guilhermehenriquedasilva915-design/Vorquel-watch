-- V1 has no direct browser/client table access.
-- Backend/control-plane access uses server-side credentials only.

alter table public.sources enable row level security;
alter table public.processing_jobs enable row level security;
alter table public.processing_runs enable row level security;
alter table public.transcripts enable row level security;
alter table public.transcript_segments enable row level security;
alter table public.speakers enable row level security;
alter table public.speaker_turns enable row level security;
alter table public.reviews enable row level security;
alter table public.artifacts enable row level security;

revoke all on table public.sources from anon, authenticated;
revoke all on table public.processing_jobs from anon, authenticated;
revoke all on table public.processing_runs from anon, authenticated;
revoke all on table public.transcripts from anon, authenticated;
revoke all on table public.transcript_segments from anon, authenticated;
revoke all on table public.speakers from anon, authenticated;
revoke all on table public.speaker_turns from anon, authenticated;
revoke all on table public.reviews from anon, authenticated;
revoke all on table public.artifacts from anon, authenticated;

comment on table public.sources is
  'Structured source metadata only. Host filesystem paths must never be stored here.';
comment on table public.reviews is
  'Append-only human review audit log.';
