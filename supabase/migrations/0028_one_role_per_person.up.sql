-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0028 — one person, one role. The database decides, not the browser.
--
-- WHY THIS EXISTS. The owner's words: "there should be a clear distinction
-- between which type of user one person is — they shouldn't create a farmer
-- account and then create a buyer account." What the code actually does today:
--
--   * update_user_profile() in app.py posts every profile save to
--     .../rest/v1/farmers, whatever role the person chose. Repo-wide the only other
--     profile-row write is supa_insert('transporters', …). No path anywhere — backend,
--     SPA, admin console, migration, script — creates a `buyers` row. Verified by
--     grep over app.py, services/, scripts/, static/ and supabase/.
--   * So the farmer/buyer/transporter distinction is not carried by any
--     server-side fact. _auth_role() reads it back from GoTrue `user_metadata.role`
--     — a field the signed-in person can rewrite through the auth SDK. The
--     standing constraint is the opposite: "do not allow users to switch between
--     farmer, buyer, transporter and administrator roles by editing a profile
--     field."
--   * `user_roles` cannot hold that role as it stands: its CHECK (0002:33) allows
--     only 'user' | 'admin' | 'superadmin'. That is an admin-privilege store, and
--     overloading it would cost an admin who also buys their admin row. But its
--     `user_id` is already the PRIMARY KEY (0002:29), so "at most one role row per
--     person" is free — this file adds the business role to that one-row-per-person
--     table rather than inventing a second store.
--
-- WHY A TRIGGER AND NOT AN RLS POLICY. An INSERT policy can express this, and it
-- was measured doing so (PGlite, tests/sql/one_role_per_person.test.mjs):
--     with check (auth.uid() = id
--                 and not exists (select 1 from public.buyers b where b.id = auth.uid()))
--   -> the second role is refused: 42501 new row violates row-level security policy.
-- Two further measurements ruled it out as the instrument:
--   1. `service_role` is bypassrls and stepped straight over the same policy
--      ("service_role under the same missing grant: OK"). render.yaml:18-21 says
--      SUPABASE_KEY IS that key, so an RLS rule would not bind the admin console —
--      the one place a second role row gets created.
--   2. The policy's subquery runs under the CALLER's own RLS and needs SELECT on
--      the sibling table; without that grant every signup dies with
--      "42501 permission denied for table buyers" — the guard would break the very
--      write it is meant to protect, and fixing it means granting reads.
-- A SECURITY DEFINER trigger holds against every role including service_role and
-- reads as its own owner, so it needs no new grant anywhere. Note also that the
-- subquery is only meaningful correlated to auth.uid(): measured, an uncorrelated
-- `not exists (select 1 from public.buyers)` passed for a different uid because
-- that caller could see zero buyers rows. Correlation is the rule; RLS visibility
-- is the trap.
--
-- WHAT THIS MIGRATION DOES
--   1. Pre-flight, in the 0025 style: refuses to install a guard the current role
--      cannot see through, instead of installing half a security control.
--   2. Adds `user_roles.business_role` — 'farmer' | 'vendor' | 'hotel' |
--      'supplier' | 'transporter', written by the backend at a person's first
--      account-type choice or by an admin. Nothing here grants `authenticated` any
--      write on user_roles: 0025:211 revoked them and the `user_roles_no_public`
--      policy refuses reads. The browser still cannot touch it; it changes only
--      through the API.
--   3. Adds BEFORE INSERT / UPDATE triggers on farmers, buyers and transporters
--      that refuse a row for a uid which already has a row in one of the other two
--      tables. Same-table writes are untouched, so editing your own profile, and
--      the admin console's updates of an existing transporter, behave as before.
--      Each table's identity column is followed, not assumed: farmers and buyers
--      key on id, transporters on supabase_uid (0014:44-50, 0026:108-114).
--   4. Narrows `authenticated`'s INSERT on farmers and buyers to the profile
--      columns, mirroring the list 0025:190-192 installed for UPDATE. This closes
--      the gap 0026 opened on purpose and no earlier test covered: a whole-table
--      INSERT privilege plus `with check (auth.uid() = id)` lets a person create
--      their own row WITH `is_verified = true` or `is_premium = true`. 0025 stopped
--      that on the UPDATE path only — tests/sql/self_signup_policies.test.mjs:254
--      asserts the UPDATE case; the INSERT case is asserted here.
--
-- CONSEQUENCES, NAMED BEFORE THEY ARE DISCOVERED
--   * An admin adding a transporter for someone who already signed up as a farmer
--     (admin_create_transporter) is now refused, by design. That call site needs a message that
--     says "this person is already registered as a farmer", and the API stage of
--     this work adds it. Until then the admin sees a failed save, not a silent one.
--   * People who ALREADY have two rows are not touched: triggers fire on writes,
--     never on existing data, and this file reads and changes no rows. Cleaning any
--     such pair is a data decision for the owner, alongside the '+10000000000'
--     placeholder accounts.
--   * A buyer still cannot be SAVED, only created. Measured in
--     tests/sql/one_role_per_person.test.mjs §4: after this file, `authenticated`
--     can INSERT its own buyers row, but the upsert shape the route actually sends
--     (`on conflict (id) do update set …`) is refused with `permission denied for
--     table buyers`, because 0025:211 revoked `authenticated`'s UPDATE on buyers
--     and the census has no buyers UPDATE policy to put back under it — farmers
--     has farmers_owner_update, buyers has only buyers_owner_select. Closing that is
--     a deliberate, separately-reviewed grant plus a buyers_owner_update policy, and
--     it belongs with the stage that starts writing buyers rows, not here.
--   * `create_order_atomic` fills orders.buyer_phone from `farmers`
--     (0015_category_order_pricing.up.sql:139, :192, :252, :288). That is one more
--     reason this file does NOT start routing buyers into `buyers`: doing it alone
--     would leave every new order with a NULL buyer_phone, which is the number the
--     seller calls. Buyer-side storage and that lookup move together, in a later
--     stage.
--
-- Re-running is safe: the column is added with IF NOT EXISTS, the function is
-- created OR REPLACE, every trigger is drop-then-create. No rows are touched.

begin;

-- ── 1. Pre-flight ────────────────────────────────────────────────────────────
-- The guard below is SECURITY DEFINER and its owner is whoever applies this file.
-- Applied as a role that does not bypass RLS, it would install happily and then
-- read the sibling tables through that role's own policies — finding nothing, and
-- allowing exactly the second role it exists to refuse. A security control that
-- silently sees nothing is worse than no control, so this stops instead.
do $$
declare
  v_bypass boolean;
begin
  select rolbypassrls into v_bypass from pg_roles where rolname = current_user;
  if v_bypass is distinct from true then
    raise exception '0028 must be applied by a role that bypasses row level security (in Supabase: postgres). Applying it as "%" installs a one-role guard that cannot see the sibling tables.',
      current_user
      using errcode = '55000',
            hint   = 'Run this file in the Supabase SQL Editor signed in as the project owner. Nothing has been committed.';
  end if;
end $$;

-- ── 2. The role, stored where the browser cannot write it ────────────────────
-- The vocabulary is the app's, taken from services/legal.py:41-50 (normalize_role)
-- and the signup select (#regRole in static/index.html) — farmer | vendor | hotel |
-- supplier — plus the transporter the portal routes on (getCurrentRole in static/index.html). 'buyer'
-- is NOT one of them: it names the table and the admin console's label, and
-- normalize_role folds an unknown value to 'vendor'. Inventing a fifth word here
-- would have made the column disagree with every screen that reads it.
alter table public.user_roles
  add column if not exists business_role text
    constraint user_roles_business_role_chk
    check (business_role in ('farmer', 'vendor', 'hotel', 'supplier', 'transporter'));

comment on column public.user_roles.business_role is
  'Which account type AgriBridge treats this person as: '
  'farmer | vendor | hotel | supplier | transporter — the values normalize_role '
  '(services/legal.py:41) and the signup form already use. One row per person is '
  'already enforced by the user_id primary key (0002:29). Written by the backend at '
  'a person''s first account-type choice, or by an admin. Never read from GoTrue '
  'user_metadata, which the signed-in person can rewrite.';

-- ── 3. One profile row per person, across the three role tables ──────────────
create or replace function public.reject_second_role_row()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  v_uid     uuid;
  v_sibling text;
  v_found   boolean;
  v_label   text;
begin
  -- Each table keys the person differently; read the column this table uses.
  v_uid := case when tg_table_name = 'transporters'
                then (to_jsonb(new) ->> 'supabase_uid')::uuid
                else new.id::uuid end;

  if v_uid is null then
    return new;   -- an unlinked row (an admin-created transporter with no account
                  -- yet). Nothing to compare it against, and the NOT NULL rules in
                  -- 0025 handle the fields that matter.
  end if;

  for v_sibling in
    select t from unnest(array['farmers', 'buyers', 'transporters']) as t
     where t <> tg_table_name
  loop
    execute format(
      'select exists (select 1 from public.%I where %s = $1)',
      v_sibling,
      case when v_sibling = 'transporters' then 'supabase_uid' else 'id' end
    ) into v_found using v_uid;

    if v_found then
      v_label := case v_sibling
                   when 'farmers'       then 'farmer'
                   when 'buyers'        then 'buyer'
                   when 'transporters'  then 'transporter'
                   else v_sibling end;
      raise exception 'this person is already registered as a %', v_label
        using hint    = 'AgriBridge allows one role per account. Edit the existing '
                        || 'profile instead of creating a second one, or recover the '
                        || 'account that already exists.',
              errcode = 'GP001';
    end if;
  end loop;

  return new;
end;
$fn$;

-- Not callable as a routine (it returns trigger), and nobody should enumerate it.
revoke all on function public.reject_second_role_row() from public;

drop trigger if exists "one_role_per_person" on public.farmers;
create trigger "one_role_per_person"
  before insert or update of id on public.farmers
  for each row execute function public.reject_second_role_row();

drop trigger if exists "one_role_per_person" on public.buyers;
create trigger "one_role_per_person"
  before insert or update of id on public.buyers
  for each row execute function public.reject_second_role_row();

drop trigger if exists "one_role_per_person" on public.transporters;
create trigger "one_role_per_person"
  before insert or update of supabase_uid on public.transporters
  for each row execute function public.reject_second_role_row();

-- ── 4. Signing yourself up cannot verify yourself ────────────────────────────
-- 0026 gave `authenticated` the right to create its own row. A whole-table INSERT
-- privilege plus `with check (auth.uid() = id)` means the columns of that row are
-- unconstrained, so a person could create themselves as is_verified / is_premium.
-- Same move 0025 made for UPDATE, applied to the write path that now exists.
revoke insert on public.farmers from authenticated;
revoke insert on public.buyers  from authenticated;

do $$
declare
  wanted text[] := array['full_name', 'phone', 'district', 'profile_photo',
                         'sub_county', 'village', 'farm_size_acres', 'crops_grown',
                         'preferred_language'];
  cols  text;
  v_tbl text;
begin
  foreach v_tbl in array array['farmers', 'buyers'] loop
    select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position)
      into cols
      from information_schema.columns c
     where c.table_schema = 'public' and c.table_name = v_tbl
       and c.column_name = any (wanted);

    if cols is null then
      raise exception '0028 found none of the profile columns it expects on public.%', v_tbl
        using hint = 'Compare the live table with supabase/base-schema.sql:6-34 before '
                     'applying. The revokes above have not been committed either.';
    end if;

    -- `id` has to be insertable or the owner-insert policy can never match.
    execute format('grant insert (id, %s) on public.%I to authenticated', cols, v_tbl);
    raise notice '0028: authenticated may now insert % on public.%',
      replace(cols, '"', ''), v_tbl;
  end loop;
end $$;

commit;
