alter table public.transcript_segments
  add column search_vector tsvector
  generated always as (
    to_tsvector('simple', coalesce(effective_text, ''))
  ) stored;

create index transcript_segments_search_vector_gin
  on public.transcript_segments
  using gin (search_vector);
