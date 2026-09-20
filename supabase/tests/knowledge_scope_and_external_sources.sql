\set ON_ERROR_STOP on

-- Invariants of 0021, 0022, 0023 and 0024.
--
-- These are the gates that decide whether LEARN + ASK can be trusted at all:
-- one client must never read another's knowledge, a secret must never become
-- knowledge, a candidate must never answer a question before a human approved
-- it, and re-learning the same bytes must not multiply anything. They are
-- asserted here rather than assumed, against a disposable PostgreSQL.

-- ---------------------------------------------------------------------------
-- 0. Structure
-- ---------------------------------------------------------------------------

do $$
begin
  if to_regprocedure('public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)') is null then
    raise exception 'register_external_source is missing';
  end if;
  if to_regprocedure('public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)') is null then
    raise exception 'create_knowledge_candidate_scoped is missing';
  end if;
  if to_regprocedure('public.search_knowledge_items_scoped(text,text,text,text,integer)') is null then
    raise exception 'search_knowledge_items_scoped is missing';
  end if;
  if to_regprocedure('public.approve_knowledge_candidates_batch(text[],text,text)') is null then
    raise exception 'approve_knowledge_candidates_batch is missing';
  end if;
end $$;

-- No scoped SECURITY DEFINER RPC may be reachable by a browser-side role.
do $
declare
  v_fn regprocedure;
begin
  foreach v_fn in array array[
    'public.search_knowledge_items_scoped(text,text,text,text,integer)'::regprocedure,
    'public.register_external_source(text,text,text,bigint,text,text,text,text,text,jsonb)'::regprocedure,
    'public.create_knowledge_candidate_scoped(text,text,text,text,text,text,text,text,text,text,text,jsonb)'::regprocedure,
    'public.approve_knowledge_candidates_batch(text[],text,text)'::regprocedure
  ] loop
    if has_function_privilege('anon', v_fn, 'EXECUTE')
       or has_function_privilege('authenticated', v_fn, 'EXECUTE') then
      raise exception 'browser-side role can execute backend-only RPC %', v_fn;
    end if;
    if not has_function_privilege('service_role', v_fn, 'EXECUTE') then
      raise exception 'service_role lost EXECUTE on %', v_fn;
    end if;
    if exists (select 1 from pg_roles where rolname = 'watch_runtime')
       and not has_function_privilege('watch_runtime', v_fn, 'EXECUTE') then
      raise exception 'watch_runtime lost EXECUTE on %', v_fn;
    end if;
  end loop;
end $;

-- ---------------------------------------------------------------------------
-- 1. SECRET is not a representable classification
-- ---------------------------------------------------------------------------

do $$
begin
  begin
    insert into public.sources
      (source_id, source_kind, content_sha256, byte_size, detected_mime,
       ingest_status, data_trust_class, data_classification)
    values
      ('src_secret_class', 'PDF', repeat('a', 64), 10, 'application/pdf',
       'READY', 'UNTRUSTED_DOCUMENT', 'SECRET');
    raise exception 'SECRET was accepted as a source classification';
  exception
    when check_violation then null;
  end;
end $$;

-- ---------------------------------------------------------------------------
-- 2. External sources: registration, idempotency, versioning
-- ---------------------------------------------------------------------------

-- A PDF can now be a source at all, which 0001 did not allow.
select public.register_external_source(
  'src_pdf_v1', 'PDF', repeat('1', 64), 2048, 'application/pdf',
  'runbook.pdf', 'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL',
  '{"origin": "operator-supplied"}'::jsonb);

do $$
declare
  v_kind  text;
  v_trust text;
  v_ver   integer;
begin
  select source_kind, data_trust_class, source_version
    into v_kind, v_trust, v_ver
  from public.sources where source_id = 'src_pdf_v1';

  if v_kind <> 'PDF' then
    raise exception 'PDF source was not registered';
  end if;
  if v_trust <> 'UNTRUSTED_DOCUMENT' then
    raise exception 'PDF source did not land in the untrusted document class';
  end if;
  if v_ver <> 1 then
    raise exception 'first version of a logical source was not 1';
  end if;
end $$;

-- Same bytes: idempotent. Nothing new was learned, so nothing is written.
select public.register_external_source(
  'src_pdf_v1_again', 'PDF', repeat('1', 64), 2048, 'application/pdf',
  'runbook.pdf', 'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL');

do $$
begin
  if (select count(*) from public.sources where logical_key = 'runbook.pdf') <> 1 then
    raise exception 're-registering identical bytes created a second row';
  end if;
  if exists (select 1 from public.sources where source_id = 'src_pdf_v1_again') then
    raise exception 'idempotent registration still inserted a new source id';
  end if;
end $$;

-- Different bytes under the same logical key: a new version that links back.
select public.register_external_source(
  'src_pdf_v2', 'PDF', repeat('2', 64), 4096, 'application/pdf',
  'runbook.pdf', 'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL');

do $$
declare
  v_ver  integer;
  v_prev text;
begin
  select source_version, supersedes_source_id into v_ver, v_prev
  from public.sources where source_id = 'src_pdf_v2';

  if v_ver <> 2 then
    raise exception 'changed bytes did not produce version 2, got %', v_ver;
  end if;
  if v_prev is distinct from 'src_pdf_v1' then
    raise exception 'version 2 does not point back at version 1';
  end if;
  -- History is preserved, never overwritten.
  if not exists (select 1 from public.sources where source_id = 'src_pdf_v1') then
    raise exception 'previous version was destroyed by the new one';
  end if;
end $$;

-- Media keeps its own ingest path.
do $$
begin
  begin
    perform public.register_external_source(
      'src_video_wrong', 'LOCAL_VIDEO', repeat('3', 64), 10, 'video/mp4');
    raise exception 'media source was admitted through the external path';
  exception
    when sqlstate '22023' then null;
  end;
end $$;

-- ---------------------------------------------------------------------------
-- 3. Human review is a real gate
-- ---------------------------------------------------------------------------

select public.create_knowledge_candidate_scoped(
  'knd_pdf_pending', 'src_pdf_v1', 'PROCEDURE', 'n8n',
  'Retry policy for HTTP Request',
  'Set retryOnFail with a bounded maxTries instead of an unbounded loop.',
  'DECLARADO', repeat('b', 64),
  'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL',
  '[{"knowledge_source_id": "ksr_pdf_p12", "locator_kind": "PDF_PAGE",
     "locator": {"page": 12}}]'::jsonb);

do $$
begin
  if (select status from vorquel_knowledge.knowledge_candidates
      where candidate_id = 'knd_pdf_pending') <> 'PENDING' then
    raise exception 'a new candidate was not PENDING';
  end if;

  -- The whole point of the gate: an unapproved candidate answers nothing.
  if exists (
    select 1 from public.search_knowledge_items_scoped(
      'retry policy', 'GLOBAL_VORQUEL', 'GLOBAL', null, 20)
  ) then
    raise exception 'a pending candidate was retrievable as approved knowledge';
  end if;
end $$;

-- Re-learning the same source with the same content hash is idempotent, and
-- says so. The Brain derives candidate_id from the content, so a replay passes
-- the identical id; reuse has to be detected from the write, not from the id.
do $$
declare
  v_reused boolean;
begin
  select reused into v_reused
  from public.create_knowledge_candidate_scoped(
    'knd_pdf_pending', 'src_pdf_v1', 'PROCEDURE', 'n8n',
    'Retry policy for HTTP Request',
    'Set retryOnFail with a bounded maxTries instead of an unbounded loop.',
    'DECLARADO', repeat('b', 64),
    'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL');

  if not v_reused then
    raise exception 'an identical replay was reported as a new candidate';
  end if;
end $$;

select public.create_knowledge_candidate_scoped(
  'knd_pdf_duplicate', 'src_pdf_v1', 'PROCEDURE', 'n8n',
  'Retry policy for HTTP Request',
  'Set retryOnFail with a bounded maxTries instead of an unbounded loop.',
  'DECLARADO', repeat('b', 64),
  'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL');

do $$
begin
  if (select count(*) from vorquel_knowledge.knowledge_candidates
      where source_id = 'src_pdf_v1') <> 1 then
    raise exception 're-learning the same content created a second candidate';
  end if;
  if (select count(*) from vorquel_knowledge.knowledge_sources
      where candidate_id = 'knd_pdf_pending') <> 1 then
    raise exception 'provenance was duplicated on re-learn';
  end if;
end $$;

-- After an explicit human approval it becomes retrievable.
select public.approve_knowledge_candidate(
  'knd_pdf_pending', 'krv_pdf_001', 'knw_pdf_retry', 'reviewed by operator');

do $$
declare
  v_kind text;
  v_page jsonb;
begin
  if not exists (
    select 1 from public.search_knowledge_items_scoped(
      'retry policy', 'GLOBAL_VORQUEL', 'GLOBAL', null, 20)
    where knowledge_id = 'knw_pdf_retry'
  ) then
    raise exception 'approved knowledge is not retrievable';
  end if;

  -- 0021 added locator columns; without 0023 the approval path silently
  -- dropped them, losing the provenance of every non-media source exactly
  -- when it became knowledge.
  select locator_kind, locator into v_kind, v_page
  from vorquel_knowledge.knowledge_sources
  where knowledge_id = 'knw_pdf_retry';

  if v_kind <> 'PDF_PAGE' then
    raise exception 'PDF provenance kind was lost on approval, got %', v_kind;
  end if;
  if v_page->>'page' <> '12' then
    raise exception 'PDF page locator was lost on approval';
  end if;
end $$;

-- 0020 made valid_from NOT NULL without a default and without updating the
-- approval path, so every approval after it failed on a not-null violation.
-- Nothing applied a migration in CI, so it stayed invisible until now.
do $$
declare
  v_from timestamptz;
  v_state text;
begin
  select valid_from, lifecycle_state into v_from, v_state
  from vorquel_knowledge.knowledge_items
  where knowledge_id = 'knw_pdf_retry';

  if v_from is null then
    raise exception 'approved knowledge has no validity start';
  end if;
  if v_state <> 'ACTIVE' then
    raise exception 'approved knowledge did not land ACTIVE, got %', v_state;
  end if;

  -- The column default covers any writer that is not this function.
  if (select column_default is null
      from information_schema.columns
      where table_schema = 'vorquel_knowledge'
        and table_name = 'knowledge_items'
        and column_name = 'valid_from') then
    raise exception 'valid_from still has no default; another writer can break';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 4. Scope isolation
-- ---------------------------------------------------------------------------

select public.register_external_source(
  'src_client_a', 'MESSAGE', repeat('4', 64), 120, 'text/plain',
  null, 'CLIENT', 'acme', 'CLIENT_CONFIDENTIAL');
select public.register_external_source(
  'src_client_b', 'MESSAGE', repeat('5', 64), 120, 'text/plain',
  null, 'CLIENT', 'globex', 'CLIENT_CONFIDENTIAL');
select public.register_external_source(
  'src_private', 'MESSAGE', repeat('6', 64), 120, 'text/plain',
  null, 'PRIVATE_TEST', 'lab', 'INTERNAL');

-- A candidate may not be filed under a scope its source does not belong to,
-- or LEARN becomes a way to launder one client's material into another scope.
do $$
begin
  begin
    perform public.create_knowledge_candidate_scoped(
      'knd_launder', 'src_client_a', 'CONCEPT', 'n8n',
      'laundered', 'moved from acme to globex', 'DECLARADO', repeat('c', 64),
      'CLIENT', 'globex', 'CLIENT_CONFIDENTIAL');
    raise exception 'a candidate was filed under a foreign scope';
  exception
    when sqlstate '22023' then null;
  end;
end $$;

select public.create_knowledge_candidate_scoped(
  'knd_acme', 'src_client_a', 'PATTERN', 'n8n',
  'acme webhook signature', 'acme signs webhooks with a shared header.',
  'OBSERVADO', repeat('d', 64), 'CLIENT', 'acme', 'CLIENT_CONFIDENTIAL');
select public.create_knowledge_candidate_scoped(
  'knd_globex', 'src_client_b', 'PATTERN', 'n8n',
  'globex webhook signature', 'globex signs webhooks with a rotating token.',
  'OBSERVADO', repeat('e', 64), 'CLIENT', 'globex', 'CLIENT_CONFIDENTIAL');
select public.create_knowledge_candidate_scoped(
  'knd_private', 'src_private', 'PATTERN', 'n8n',
  'private lab webhook', 'internal experiment, not for any client.',
  'HIPOTESE', repeat('f', 64), 'PRIVATE_TEST', 'lab', 'INTERNAL');

select public.approve_knowledge_candidate('knd_acme', 'krv_acme_001', 'knw_acme');
select public.approve_knowledge_candidate('knd_globex', 'krv_globex_001', 'knw_globex');
select public.approve_knowledge_candidate('knd_private', 'krv_private_001', 'knw_private');

-- The query is deliberately NULL here, which returns every ACTIVE item the
-- scope may see. A text query would make this test about full-text matching
-- instead of about isolation -- and websearch_to_tsquery ANDs its terms, so a
-- multi-word query matches nothing, array_agg returns NULL, and every
-- `NULL @> array[...]` is NULL rather than false. The whole section would then
-- pass without proving anything. coalesce below keeps that failure mode closed.
do $$
declare
  v_ids text[];
begin
  -- GLOBAL sees only GLOBAL.
  select coalesce(array_agg(knowledge_id order by knowledge_id), '{}') into v_ids
  from public.search_knowledge_items_scoped(
    null::text, 'GLOBAL_VORQUEL', 'GLOBAL', null, 50);
  if not (v_ids @> array['knw_pdf_retry']) then
    raise exception 'GLOBAL retrieval returned nothing; the test would be vacuous';
  end if;
  if v_ids @> array['knw_acme'] or v_ids @> array['knw_globex']
     or v_ids @> array['knw_private'] then
    raise exception 'GLOBAL retrieval returned scoped knowledge: %', v_ids;
  end if;

  -- CLIENT acme sees GLOBAL + acme.
  select coalesce(array_agg(knowledge_id order by knowledge_id), '{}') into v_ids
  from public.search_knowledge_items_scoped(null::text, 'CLIENT', 'acme', null, 50);
  if not (v_ids @> array['knw_acme']) then
    raise exception 'acme cannot see its own knowledge: %', v_ids;
  end if;
  if not (v_ids @> array['knw_pdf_retry']) then
    raise exception 'acme cannot see GLOBAL knowledge: %', v_ids;
  end if;

  -- ...and never another client, or a private test scope.
  if v_ids @> array['knw_globex'] then
    raise exception 'CLIENT ISOLATION BROKEN: acme retrieved globex knowledge';
  end if;
  if v_ids @> array['knw_private'] then
    raise exception 'acme retrieved PRIVATE_TEST knowledge';
  end if;

  -- Symmetric check, so the rule is not accidentally one-directional.
  select coalesce(array_agg(knowledge_id order by knowledge_id), '{}') into v_ids
  from public.search_knowledge_items_scoped(null::text, 'CLIENT', 'globex', null, 50);
  if not (v_ids @> array['knw_globex']) then
    raise exception 'globex cannot see its own knowledge: %', v_ids;
  end if;
  if v_ids @> array['knw_acme'] then
    raise exception 'CLIENT ISOLATION BROKEN: globex retrieved acme knowledge';
  end if;

  -- PRIVATE_TEST is not a backdoor into client scopes either.
  select coalesce(array_agg(knowledge_id order by knowledge_id), '{}') into v_ids
  from public.search_knowledge_items_scoped(null::text, 'PRIVATE_TEST', 'lab', null, 50);
  if v_ids @> array['knw_acme'] or v_ids @> array['knw_globex'] then
    raise exception 'PRIVATE_TEST retrieved client knowledge: %', v_ids;
  end if;
end $$;

-- A malformed or widening scope request fails instead of returning more.
do $$
begin
  begin
    perform public.search_knowledge_items_scoped('x', 'CLIENT', 'GLOBAL', null, 10);
    raise exception 'CLIENT was allowed to address the reserved GLOBAL id';
  exception
    when sqlstate '22023' then null;
  end;

  begin
    perform public.search_knowledge_items_scoped('x', 'EVERYONE', 'GLOBAL', null, 10);
    raise exception 'an unknown scope_type was accepted';
  exception
    when sqlstate '22023' then null;
  end;

  begin
    perform public.search_knowledge_items_scoped('x', 'CLIENT', 'acme; drop table', null, 10);
    raise exception 'a malformed scope_id was accepted';
  exception
    when sqlstate '22023' then null;
  end;
end $$;

-- ---------------------------------------------------------------------------
-- 5. A locator is an address, never a payload or a command
-- ---------------------------------------------------------------------------

do $$
begin
  begin
    insert into vorquel_knowledge.knowledge_sources
      (knowledge_source_id, knowledge_id, source_id, locator_kind, locator)
    values
      ('ksr_bad_locator', 'knw_pdf_retry', 'src_pdf_v1', 'REPO_FILE',
       '{"token": "ghp_notarealsecret"}'::jsonb);
    raise exception 'a secret-bearing locator was accepted';
  exception
    when check_violation then null;
  end;

  begin
    insert into vorquel_knowledge.knowledge_sources
      (knowledge_source_id, knowledge_id, source_id, locator_kind, locator)
    values
      ('ksr_bad_kind', 'knw_pdf_retry', 'src_pdf_v1', 'ARBITRARY',
       '{"path": "README.md"}'::jsonb);
    raise exception 'an unknown locator kind was accepted';
  exception
    when check_violation then null;
  end;
end $$;

-- ---------------------------------------------------------------------------
-- 6. Batch review stays a human-sized gesture
-- ---------------------------------------------------------------------------

do $$
begin
  begin
    perform public.approve_knowledge_candidates_batch(
      array(select 'knd_' || lpad(i::text, 4, '0') from generate_series(1, 51) i),
      'krv_batch_big');
    raise exception 'a batch larger than a human can read was accepted';
  exception
    when sqlstate '22023' then null;
  end;

  begin
    perform public.approve_knowledge_candidates_batch(array[]::text[], 'krv_batch_empty');
    raise exception 'an empty batch was accepted';
  exception
    when sqlstate '22023' then null;
  end;

  begin
    perform public.approve_knowledge_candidates_batch(array['knd_x'], 'not_a_review_prefix');
    raise exception 'a malformed review prefix was accepted';
  exception
    when sqlstate '22023' then null;
  end;
end $$;

-- A batch over ids that were actually presented approves them and reports
-- per-candidate outcomes, so one bad id cannot silently drop the rest.
select public.register_external_source(
  'src_batch', 'MARKDOWN', repeat('7', 64), 300, 'text/markdown',
  null, 'GLOBAL_VORQUEL', 'GLOBAL', 'INTERNAL');

select public.create_knowledge_candidate_scoped(
  'knd_batch_one', 'src_batch', 'CONCEPT', 'n8n', 'batch one', 'first.',
  'DECLARADO', repeat('8', 64));
select public.create_knowledge_candidate_scoped(
  'knd_batch_two', 'src_batch', 'CONCEPT', 'n8n', 'batch two', 'second.',
  'DECLARADO', repeat('9', 64));

do $$
declare
  v_ok    integer;
  v_fail  integer;
begin
  select count(*) filter (where approved),
         count(*) filter (where not approved)
    into v_ok, v_fail
  from public.approve_knowledge_candidates_batch(
    array['knd_batch_one', 'knd_batch_two', 'knd_does_not_exist'],
    'krv_batch_ok', 'reviewed together');

  if v_ok <> 2 then
    raise exception 'batch approved % candidates, expected 2', v_ok;
  end if;
  if v_fail <> 1 then
    raise exception 'batch did not report the unknown candidate as failed';
  end if;

  -- Every approval leaves an audit row attributed to a human.
  if (select count(*) from vorquel_knowledge.knowledge_reviews
      where candidate_id in ('knd_batch_one', 'knd_batch_two')
        and action = 'APPROVE' and actor_type = 'HUMAN') <> 2 then
    raise exception 'batch approval did not leave a human audit trail';
  end if;
end $$;
