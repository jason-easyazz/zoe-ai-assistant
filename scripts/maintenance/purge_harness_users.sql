-- purge_harness_users.sql - OWNER STEP (P-2, deep dive 2, 2026-10-19). Never run by an agent.
--
-- 50 of the 61 `users` rows on the live box are harness ids (the Samantha bar, day-sim, person bench, load
-- probes). This removes those ids and every Postgres row keyed to them. The id rule is THE synthetic rule,
-- `services/zoe-data/user_filters.py::SYNTHETIC_USER_RE` (case-insensitive), byte-pinned to the pattern below
-- by tests/unit/test_purge_harness_users_sql.py. Guest sentinels never match it. Ids the operator re-admitted
-- through ZOE_SYNTHETIC_USER_ALLOWLIST must be passed as -v keep_ids=a,b (they are then never touched).
--
-- psql is not installed on the Jetson host: run it inside the database container, feeding the file on stdin
-- (PSQL below = `docker exec -i zoe-database psql -U <db user> -d <db name>`, the user/db from POSTGRES_URL in
-- services/zoe-data/.env - never paste the password on a command line):
-- 1) DRY RUN (default; a READ ONLY transaction - nothing can be written):
--      PSQL -v ON_ERROR_STOP=1 < scripts/maintenance/purge_harness_users.sql
--    Read every NOTICE: the synthetic ids found in `users`, and the per-table row counts keyed to them.
--    If any id in the list is a PERSON, stop and add it to keep_ids (or to ZOE_SYNTHETIC_USER_ALLOWLIST).
-- 2) REHEARSAL (deletes inside a transaction, then ROLLBACK - proves the FK order works, keeps nothing):
--      PSQL -v ON_ERROR_STOP=1 -v purge=1 < scripts/maintenance/purge_harness_users.sql
-- 3) APPLY (same, but COMMIT). Take a backup first (pg_dump of the zoe database), outside the night window:
--      PSQL -v ON_ERROR_STOP=1 -v purge=1 -v commit=1 < scripts/maintenance/purge_harness_users.sql
--
-- Scope: the `public` schema of the zoe database only - every BASE TABLE with a `user_id`, `owner_user_id` or
-- `zoe_user_id` column, `chat_messages` through their `chat_sessions` (a session is taken only when NO turn in
-- it names a non-synthetic user in metadata->>'user_id'), and `users` itself last. Deletes run in up to 6
-- passes; a table blocked by a foreign key is retried in the next pass (savepoint per table), and a table
-- still blocked after the last pass aborts the WHOLE transaction (nothing is half-applied).
-- NOT in scope: the MemPalace / Chroma store (use the existing route per harness id:
-- POST /api/memories/users/<id>/forget-synthetic, or the bar's own teardown), the Flue sidecar's session
-- store, zoe-auth's database, the JSONL logs. A NOTICE prints each table's count; no row content is printed.

\set ON_ERROR_STOP on
\if :{?purge}
\else
  \set purge 0
\endif
\if :{?commit}
\else
  \set commit 0
\endif
\if :{?keep_ids}
\else
  \set keep_ids ''
\endif

-- user_filters.SYNTHETIC_USER_RE, in POSIX ERE (identical semantics; pinned by the test named above)
\set synthetic_re '^((test|probe|demo|ci|e2e|bench)[-_]|zoe[-_](hammer|tok)[-_][0-9]+$|zoe-(mempalace-baseline|prompt-packet-measure)$)'

BEGIN;
\if :purge
\else
SET TRANSACTION READ ONLY;
\endif
SELECT set_config('zoe.synthetic_re', :'synthetic_re', true) AS synthetic_re,
       set_config('zoe.keep_ids', :'keep_ids', true) AS keep_ids;

-- ── the ids (always printed) ─────────────────────────────────────────────────────────────────────────────────
SELECT u.id AS synthetic_user_id
  FROM users u
 WHERE u.id ~* current_setting('zoe.synthetic_re')
   AND NOT (u.id = ANY (coalesce(string_to_array(nullif(current_setting('zoe.keep_ids'), ''), ','), ARRAY[]::text[])))
 ORDER BY 1;

-- ── per-table counts (always printed) ────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
  pat  text   := current_setting('zoe.synthetic_re');
  keep text[] := coalesce(string_to_array(nullif(current_setting('zoe.keep_ids'), ''), ','), ARRAY[]::text[]);
  r    record;
  n    bigint;
BEGIN
  FOR r IN
    SELECT c.table_name, c.column_name
      FROM information_schema.columns c
      JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
     WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
       AND c.column_name IN ('user_id', 'owner_user_id', 'zoe_user_id')
     ORDER BY 1, 2
  LOOP
    EXECUTE format('SELECT count(*) FROM %I WHERE %I::text ~* $1 AND NOT (%I::text = ANY ($2))',
                   r.table_name, r.column_name, r.column_name) INTO n USING pat, keep;
    IF n > 0 THEN
      RAISE NOTICE 'dry-run  %.%  rows=%', r.table_name, r.column_name, n;
    END IF;
  END LOOP;
  SELECT count(*) INTO n FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id
   WHERE s.user_id ~* pat AND NOT (s.user_id = ANY (keep))
     AND NOT EXISTS (SELECT 1 FROM chat_messages m2 WHERE m2.session_id = s.id
                       AND coalesce(m2.metadata::jsonb ->> 'user_id', '') <> ''
                       AND (m2.metadata::jsonb ->> 'user_id' !~* pat OR m2.metadata::jsonb ->> 'user_id' = ANY (keep)));
  RAISE NOTICE 'dry-run  chat_messages (via chat_sessions)  rows=%', n;
  SELECT count(*) INTO n FROM users WHERE id ~* pat AND NOT (id = ANY (keep));
  RAISE NOTICE 'dry-run  users.id  rows=%', n;
END $$;

\if :purge
-- ── the purge (only with -v purge=1) ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
  pat     text   := current_setting('zoe.synthetic_re');
  keep    text[] := coalesce(string_to_array(nullif(current_setting('zoe.keep_ids'), ''), ','), ARRAY[]::text[]);
  r       record;
  n       bigint;
  pass    int;
  pending text[];
  blocked text[];
  sessions text[];
BEGIN
  -- the sessions to take: owned by a synthetic id, and no turn in them names a real user
  SELECT coalesce(array_agg(s.id), ARRAY[]::text[]) INTO sessions FROM chat_sessions s
   WHERE s.user_id ~* pat AND NOT (s.user_id = ANY (keep))
     AND NOT EXISTS (SELECT 1 FROM chat_messages m2 WHERE m2.session_id = s.id
                       AND coalesce(m2.metadata::jsonb ->> 'user_id', '') <> ''
                       AND (m2.metadata::jsonb ->> 'user_id' !~* pat OR m2.metadata::jsonb ->> 'user_id' = ANY (keep)));
  DELETE FROM chat_messages WHERE session_id = ANY (sessions);
  GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'purge    chat_messages  rows=%', n;

  SELECT array_agg(format('%I.%I', c.table_name, c.column_name) ORDER BY c.table_name) INTO pending
    FROM information_schema.columns c
    JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
   WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
     AND c.column_name IN ('user_id', 'owner_user_id', 'zoe_user_id')
     AND c.table_name <> 'chat_sessions';
  pending := coalesce(pending, ARRAY[]::text[]) || ARRAY['chat_sessions.user_id'];

  FOR pass IN 1..6 LOOP
    blocked := ARRAY[]::text[];
    FOR r IN SELECT split_part(x, '.', 1) AS tbl, split_part(x, '.', 2) AS col FROM unnest(pending) AS x LOOP
      BEGIN
        IF r.tbl = 'chat_sessions' THEN
          DELETE FROM chat_sessions WHERE id = ANY (sessions);
        ELSE
          EXECUTE format('DELETE FROM %I WHERE %I::text ~* $1 AND NOT (%I::text = ANY ($2))', r.tbl, r.col, r.col)
            USING pat, keep;
        END IF;
        GET DIAGNOSTICS n = ROW_COUNT;
        IF n > 0 THEN RAISE NOTICE 'purge    %.%  rows=%  (pass %)', r.tbl, r.col, n, pass; END IF;
      EXCEPTION WHEN foreign_key_violation THEN
        blocked := blocked || format('%s.%s', r.tbl, r.col);
      END;
    END LOOP;
    pending := blocked;
    EXIT WHEN cardinality(pending) = 0;
  END LOOP;
  IF cardinality(pending) > 0 THEN
    RAISE EXCEPTION 'still blocked by a foreign key after 6 passes: % - nothing applied', pending;
  END IF;

  -- users last, one id at a time: an id still referenced (e.g. it owns a session a real member also spoke in)
  -- is KEPT and named, instead of aborting the whole purge
  n := 0;
  FOR r IN SELECT id FROM users WHERE id ~* pat AND NOT (id = ANY (keep)) ORDER BY id LOOP
    BEGIN
      DELETE FROM users WHERE id = r.id;
      n := n + 1;
    EXCEPTION WHEN foreign_key_violation THEN
      RAISE NOTICE 'kept     users.id=%  (still referenced by a row this purge does not own - review by hand)', r.id;
    END;
  END LOOP;
  RAISE NOTICE 'purge    users  rows=%', n;
END $$;
\endif

\if :commit
  \if :purge
COMMIT;
\echo 'COMMITTED.'
  \else
ROLLBACK;
\echo 'commit=1 without purge=1: nothing to commit (dry run).'
  \endif
\else
ROLLBACK;
\if :purge
\echo 'REHEARSAL: every delete above was ROLLED BACK. Re-run with -v commit=1 to apply.'
\else
\echo 'DRY RUN: read-only, nothing written.'
\endif
\endif
