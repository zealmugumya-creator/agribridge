-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0026 — A person may create their own profile row. Nobody may do it for them.
--
-- WHY THIS EXISTS. The 2026-11-05 RLS census the owner ran says, for the three
-- role tables and the role store:
--
--   farmers       farmers_owner_update        UPDATE  authenticated
--   farmers       farmers_public_select       SELECT  (all roles)
--   buyers        buyers_owner_select          SELECT  authenticated
--   transporters   transporter read own        SELECT  authenticated
--   transporters   transporter update own…      UPDATE  authenticated
--   transporters   transporters_service_role    *      service_role
--   user_roles     user_roles_no_public         *      (all roles)
--   user_roles     user_roles_service            *     service_role
--   rls_enabled = true on every one of them; rls_forced = false.
--
-- There is no INSERT policy on any of them. So no signed-in browser and no
-- unauthenticated request can create a profile row at all — Postgres refuses it
-- with 42501 before the row, the phone or the uniqueness index is ever consulted.
--
-- That was the mechanism I predicted. It is NOT the live one, and the file that
-- settles it is in this repo: render.yaml:18-21 declares SUPABASE_KEY with the
-- comment "Set this in Render from Supabase's service-role/secret key", and
-- .env.example:15 says "SERVICE-ROLE key — backend ONLY". Two independent
-- declarations agree, so the branch that has been running is the second one:
--   * SUPABASE_KEY is a service-role key. `service_role` is `bypassrls`, so the
--     missing INSERT policy never blocked a signup. Profile rows have been
--     created, every time, by a role that no policy can see.
--   * The publishable-key branch ("refused by the missing policy, which is why
--     the old account is not accessible") was an inference and is recorded here
--     as wrong. The complaints have different causes and they are in
--     docs/IMPLEMENTATION_STATUS.md §N9: /api/user/profile writes `farmers` for
--     everyone (app.py:5583), no code path anywhere creates a `buyers` row, and
--     the one-phone check reads `farmers` only (app.py:5566).
--
-- So what is this migration for? It is the precondition for the fix, not the
-- cause of the bug. app.py now sends the CALLER's verified token for that write
-- (6a063fa), and from the moment this file is applied the profile save is checked
-- by farmers_owner_insert instead of sailing past every policy in the database.
-- Without it, routing the write through the caller's token would fail.
--
-- WHAT THIS MIGRATION DOES
--   1. Adds the missing half of the guarantee the UPDATE policies already give:
--      an authenticated user may INSERT a row whose identity column IS their own
--      uid. farmers and buyers key on id; transporters keys on supabase_uid
--      (0014:44-50), so the check follows each table, not a guess.
--      This does not weaken RLS — it cannot. `with check (auth.uid() = id)` lets a
--      person create exactly one row, their own, and refuses every other id. The
--      existing policies stay exactly as they are; nothing is broadened for
--      anon or for anyone writing another person's row.
--   2. Revokes insert/update/delete/truncate/references/trigger from `anon` on
--      all four tables. The census is what makes this safe to ship: RLS is enabled
--      on each of them and no policy mentions anon, so an anon write is already
--      refused today. The revoke removes the second door, the one that only opens
--      if someone later turns RLS off on one of these tables in the dashboard —
--      which is precisely how this project's live schema drifted from the repo's.
--      Reads are untouched: farmers_public_select is `to public`, and the public
--      browse pages and every supa_get read path keep working.
--
-- WHAT IT DELIBERATELY DOES NOT DO
--   It does not touch `farmers_phone_unique` or its `WHERE (phone <>
--   '+10000000000')` exemption, and it deletes no rows. Cleaning the eight
--   placeholder accounts is a data change with listings/orders/reviews/payouts
--   pointing at those ids, and it needs the owner's decision first.
--   It does not add INSERT policies for any other table. Each table's write
--   identity is a separate measurement; the four here are the four the census
--   covered.
--
-- CONSEQUENCE WORTH KNOWING BEFORE APPLYING
--   The four transporters writes in the admin console — supa_update('transporters',
--   …) at app.py:3532, :3541, :3561 and :3646, plus supa_insert at :3504 — send
--   SUPABASE_KEY, which render.yaml:18-21 declares as the service-role key. That
--   role is bypassrls, so nothing in this file changes them: the admin console
--   behaves exactly as it did a minute ago. The SQL test asserts this rather than
--   leaving it implied, and 0028 is the file that constrains those writes, with a
--   trigger — the only instrument a bypassrls role cannot step over.
--
--   /api/user/profile is different, and deliberate: it now sends the CALLER's
--   verified token first, so from the moment this migration is applied a profile
--   save is checked by farmers_owner_insert rather than bypassing it. On a project
--   where 0026 is not applied yet the route retries once with the backend key and
--   logs profile.policy_missing — because applying migrations here is a manual
--   dashboard step, and signup must not go down while one waits.
--
-- Re-running is safe: every policy is drop-then-create, every revoke is
-- idempotent. No rows are touched.

begin;

-- ── 1. The row-scoped INSERT policies ───────────────────────────────────────
-- Written as drop-then-create with quoted names, matching 0014's style, so a
-- re-run cannot leave two policies on one table and a dashboard-renamed policy is
-- not silently kept alongside the new one.

drop policy if exists "farmers_owner_insert" on public.farmers;
create policy "farmers_owner_insert" on public.farmers
  for insert to authenticated
  with check (auth.uid() = id);

drop policy if exists "buyers_owner_insert" on public.buyers;
create policy "buyers_owner_insert" on public.buyers
  for insert to authenticated
  with check (auth.uid() = id);

-- transporters has no auth.uid()-shaped primary key: 0014 keys the row on
-- supabase_uid, so the INSERT check has to use the same column or the policy
-- contradicts the UPDATE policy two lines above it in the census.
drop policy if exists "transporters_owner_insert" on public.transporters;
create policy "transporters_owner_insert" on public.transporters
  for insert to authenticated
  with check (auth.uid() = supabase_uid);

-- ── 2. anon loses the write privileges RLS already refuses it ───────────────
-- `public` is included because Supabase's default privileges put it on some
-- projects and a reader of pg_class.relacl later would treat its absence as a
-- fact about the schema. Postgres answers a revoke from a grantee that holds
-- nothing with a warning, not an error, so listing it costs nothing.
revoke insert, update, delete, truncate, references, trigger
  on public.farmers, public.buyers, public.transporters, public.user_roles
  from anon, public;

commit;
