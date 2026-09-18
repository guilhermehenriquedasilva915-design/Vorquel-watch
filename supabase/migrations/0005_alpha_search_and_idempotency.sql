create unique index processing_jobs_idempotency_uq
  on public.processing_jobs (
    source_id,
    mode,
    config_hash,
    pipeline_version
  )
  where status in ('QUEUED', 'RUNNING', 'SUCCEEDED');

create or replace function public.search_transcript_segments(
  p_query text,
  p_source_ids text[],
  p_start_ms bigint default null,
  p_end_ms bigint default null,
  p_limit integer default 20
)
returns table (
  segment_id text,
  transcript_id text,
  source_id text,
  ordinal integer,
  start_ms bigint,
  end_ms bigint,
  effective_text text,
  speaker_id text,
  review_status text,
  rank real
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    s.segment_id,
    s.transcript_id,
    s.source_id,
    s.ordinal,
    s.start_ms,
    s.end_ms,
    s.effective_text,
    s.speaker_id,
    s.review_status,
    ts_rank_cd(
      s.search_vector,
      plainto_tsquery('simple', left(coalesce(p_query, ''), 500))
    )::real as rank
  from public.transcript_segments s
  where
    cardinality(p_source_ids) between 1 and 25
    and s.source_id = any(p_source_ids)
    and (p_start_ms is null or s.end_ms >= p_start_ms)
    and (p_end_ms is null or s.start_ms <= p_end_ms)
    and s.search_vector @@ plainto_tsquery(
      'simple',
      left(coalesce(p_query, ''), 500)
    )
  order by rank desc, s.start_ms asc
  limit least(greatest(coalesce(p_limit, 20), 1), 50);
$$;

revoke all on function public.search_transcript_segments(
  text, text[], bigint, bigint, integer
) from public, anon, authenticated;

grant execute on function public.search_transcript_segments(
  text, text[], bigint, bigint, integer
) to service_role;
