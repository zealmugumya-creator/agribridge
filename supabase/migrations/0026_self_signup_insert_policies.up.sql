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
-- That is the mechanism behind the complaint "one person created two accounts,
-- which should not be allowed" and "people with old accounts cannot get into
-- them" — with one dependency stated plainly, because it is the thing this repo
-- cannot measure. The signup flow (static/index.html:10076-10104) creates the
-- auth user with GoTrue, then calls saveProfileWithRetry (:3409), which POSTs to
-- /api/user/profile. What role that write reaches PostgREST with depends on the
-- value of SUPABASE_KEY on Render:
--   * the publishable key  -> role anon -> refused by the missing policy, because
--     RLS applies to anon and no policy grants it. The function retries twice,
--     gives up, and logs "Profile could not be saved; queued to retry on next
--     visit" — and the retry only ever runs from localStorage on the same device
--     (:10102, flushPendingProfiles). The person is left with an auth account that
--     has no profile: exactly what "the old account is not accessible" means in
--     the WhatsApp thread the owner forwarded. They then register again with a
--     second email, and the head count grows.
--   * the service-role key (what .env.example:15 says it should be) -> bypasses
--     RLS, the row is created, and these policies are never exercised at all.
-- The ten-second test that settles it is in docs/IMPLEMENTATION_STATUS.md: sign
-- up on the live site with a real name and +256 number, then look for the row.
-- Either way this migration is the right half of the fix, and app.py now sends
-- the caller's own token for that write, so the answer stops mattering.
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
--   …) at app.py:3532, :3541, :3561 and :3646 — send SUPABASE_KEY. If that value is
--   the publishable key they run as anon and this migration changes nothing
--   observable: the SQL test reproduces that they are refused before it (by the
--   missing policy) and after it (by the privilege). If it is the service-role key
--   they bypass RLS and are untouched by anything in this file. Neither branch is
--   a new breakage, and both are named so nobody discovers them as a surprise.
--   Fixing those admin actions is the next stage, not this one.
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
