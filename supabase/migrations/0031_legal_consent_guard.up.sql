-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0031 — the database itself refuses a marketplace write from someone who has not
-- accepted the current agreements. The Flask gate is no longer the only door.
--
-- WHY THIS EXISTS. L2c1 (52d931c) put a consent check in a Flask before_request
-- hook, and L2c2 (4c4cc38) made the role it checks authoritative. Both only run
-- when a request goes THROUGH the backend. The browser does not always do that.
-- supabase/base-schema.sql grants `authenticated` direct write policies on the
-- marketplace tables, and static/index.html exercises them with the caller's own
-- token via sb()/sbInsert()/sbUpdate() (index.html:3395-3493):
--
--   listings           INSERT/UPDATE   listings_owner_insert / _owner_update      (base-schema:260-261)
--   animal_listings    INSERT/UPDATE   animal_listings_owner_insert / _update     (base-schema:248-249)
--   supplier_products  INSERT/UPDATE/DELETE  supplier_products_owner_*            (base-schema:276-278)
--   reviews            INSERT/UPDATE/DELETE  reviews_verified_insert / _owner_*   (base-schema:269-275)
--   community_posts    INSERT          community_posts_auth_insert                (base-schema:252)
--   orders             UPDATE          orders_farmer_update                       (base-schema:264)
--
-- and three SECURITY DEFINER RPCs are granted to `authenticated` and write the
-- same tables without touching Flask:
--   create_order_atomic  -> orders        (0015:314)
--   set_order_status     -> orders        (0015:369)
--   accept_offer / reject_offer -> order_offers  (0005:195, :215)
--
-- A signed-in person who has NOT accepted the current terms can therefore list
-- produce, post a review, place an order or accept an offer by calling PostgREST
-- or the RPC directly, and the L2c1 gate never sees it. The brief is explicit:
-- "Do not rely solely on frontend redirects. Direct API requests must also be
-- checked." This file closes that independent bypass at the only layer every one
-- of those paths shares — the table write itself.
--
-- WHY A TRIGGER AND NOT AN RLS POLICY. Same measurement that decided 0028: an RLS
-- policy is stepped over by `service_role` (bypassrls), and a policy subquery runs
-- under the caller's own RLS so it cannot reliably read user_roles / legal_acceptances.
-- A BEFORE-row SECURITY DEFINER trigger fires for EVERY role — a bypassrls role
-- cannot step over a trigger — and reads as its own owner, so it needs no new grant.
-- It also fires inside a SECURITY DEFINER RPC: auth.uid() reads the request JWT
-- (request.jwt.claim.sub), which PostgREST sets from the CALLER's token and which
-- does not change when the function body runs as its definer. So one trigger on
-- `orders` guards both a direct PATCH and create_order_atomic / set_order_status,
-- and one on `order_offers` guards accept_offer / reject_offer — no RPC body is
-- edited, which keeps this file additive and free of version drift.
--
-- WHAT THIS MIGRATION DOES
--   1. legal_current_requirements — the documents each role must currently accept,
--      one row per (role, doc_type), carrying the current version and the
--      last_material_version floor. The backend syncs it from the built manifest
--      (services/legal.py) through sync_legal_current_requirements(), an atomic
--      replace. This is the database-resident copy of the same requirement set the
--      Flask gate reads, so the two cannot disagree about what is current.
--   2. legal_consent_complete(uid) — resolves the caller's role with the SAME
--      priority as app.py (_resolve_account_type): user_roles.business_role first,
--      then an admin/superadmin privilege (mapped to vendor legal docs, as
--      _legal_role does), then the profile fallback (farmers/transporters/buyers).
--      It then checks every current requirement for that role against
--      legal_acceptances using the same satisfaction rule as LegalService._satisfied
--      (current version, or any version at/after the last material floor).
--   3. _enforce_legal_consent() — a BEFORE INSERT/UPDATE/DELETE trigger function
--      installed on the six marketplace tables above (and order_offers when 0004
--      is present). It refuses the write with 42501 when consent is incomplete.
--
-- WHAT IT DELIBERATELY DOES NOT GUARD (the exemptions the brief names)
--   * farmers / buyers / transporters / user_roles — profile bootstrap and the
--     one-time account-type choice happen BEFORE consent is recorded; guarding them
--     would block signup itself. The Flask gate exempts the same paths.
--   * legal_* tables and record_legal_acceptance — viewing documents and recording
--     acceptance must always work; that is how a user gets compliant.
--   * auth, sign-out, request_account_deletion — never gated.
--   * contact_messages / disease_reports / price_alerts / vet_bookings /
--     supply_orders — these are `to public` INSERT (anonymous support forms), not
--     authenticated marketplace writes; anon has no auth.uid() and is out of scope.
--
-- TWO SAFETY PROPERTIES, BOTH MEASURED IN tests/sql/legal_consent_guard.test.mjs
--   * The backend is unaffected. It connects with the service-role key, which
--     carries no end-user JWT, so auth.uid() is null and the trigger returns early.
--     Those writes are already gated by Flask (L2c1) and bootstrap writes happen
--     pre-consent. anon also has no uid; RLS still refuses anon writes to these
--     tables exactly as before. Only a real signed-in caller (auth.uid() set) is
--     checked.
--   * Applying this file changes nothing until the owner enables enforcement.
--     legal_current_requirements starts EMPTY, and an empty requirement set means
--     "nothing is required" -> legal_consent_complete returns true -> every write
--     passes, exactly as today. The backend only populates the table when
--     ENFORCE_LEGAL_ACCEPTANCE is on (app.py startup + the gate's lazy retry). So
--     the database guard and the Flask gate activate together, under the same
--     owner-controlled flag, and this migration is safe to apply ahead of it.
--
-- Re-running is safe: the table is CREATE IF NOT EXISTS, every function is
-- CREATE OR REPLACE, every trigger is drop-then-create, the order_offers trigger is
-- created only if that table exists. No existing row is read-modified or deleted;
-- the only data written is the new requirements table, by the backend, on sync.

begin;

-- ── 0. Pre-flight ────────────────────────────────────────────────────────────
-- The guard is SECURITY DEFINER; its owner is whoever applies this file. Applied
-- as a role that does not bypass RLS it would read user_roles / legal_acceptances
-- through that role's own policies, see nothing, and allow exactly the writes it
-- exists to refuse. A security control that silently sees nothing is worse than
-- none, so stop instead (same pre-flight as 0028).
do $$
declare
  v_bypass boolean;
begin
  select rolbypassrls into v_bypass from pg_roles where rolname = current_user;
  if v_bypass is distinct from true then
    raise exception '0031 must be applied by a role that bypasses row level security (in Supabase: postgres). Applying it as "%" installs a consent guard that cannot read the role store or acceptance evidence.',
      current_user
      using errcode = '55000',
            hint   = 'Run this file in the Supabase SQL Editor signed in as the project owner. Nothing has been committed.';
  end if;
end $$;

-- ── 1. The current requirement set, resident in the database ─────────────────
create table if not exists public.legal_current_requirements (
  role text not null,
  doc_type text not null,
  version text not null,
  last_material_version text not null,
  requires_acceptance text not null default 'accept'
    check (requires_acceptance in ('accept','acknowledge')),
  synced_at timestamptz not null default now(),
  primary key (role, doc_type),
  constraint legal_req_role_chk check (
    role in ('farmer','vendor','hotel','supplier','transporter','exporter')),
  constraint legal_req_version_fmt check (version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  constraint legal_req_floor_fmt check (last_material_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$')
);

comment on table public.legal_current_requirements is
  'The documents each account role must currently accept, synced from the built '
  'manifest (static/legal/manifest.json) by the backend only when enforcement is '
  'on. Empty => the database consent guard is inactive (fail-open), so applying '
  '0031 ahead of the ENFORCE_LEGAL_ACCEPTANCE flag changes nothing. One row per '
  '(role, doc_type); last_material_version is the floor for a non-material '
  'correction, mirroring LegalService._satisfied (services/legal.py:126-133).';

alter table public.legal_current_requirements enable row level security;

drop policy if exists legal_req_service on public.legal_current_requirements;
create policy legal_req_service on public.legal_current_requirements
  for all to service_role using (true) with check (true);

-- The guard reads this table as its definer; no client role needs access.
revoke all on public.legal_current_requirements from anon, authenticated, public;
grant all on public.legal_current_requirements to service_role;

-- ── 2. Semver comparison, matching the backend's tuple compare ───────────────
-- _satisfied() compares (major, minor, patch) tuples. Postgres row comparison is
-- lexicographic, so (a,b,c) >= (d,e,f) is the same ordering. Versions are validated
-- as X.Y.Z by the table checks, so the casts are safe.
create or replace function public._legal_ver_ge(v text, floor text)
returns boolean language sql immutable parallel safe as $$
  select (split_part(v, '.', 1)::int, split_part(v, '.', 2)::int, split_part(v, '.', 3)::int)
      >= (split_part(floor, '.', 1)::int, split_part(floor, '.', 2)::int, split_part(floor, '.', 3)::int);
$$;

-- ── 3. Does this user satisfy every current requirement for their role? ──────
create or replace function public.legal_consent_complete(p_uid uuid)
returns boolean
language plpgsql stable
security definer set search_path = public, pg_temp
as $fn$
declare
  v_business text;
  v_priv     text;
  v_role     text;
  v_btype    text;
  r          record;
  v_ok       boolean;
begin
  -- No end-user identity: nothing to enforce (the trigger already short-circuits
  -- on auth.uid() null; this keeps the function safe to call directly too).
  if p_uid is null then
    return true;
  end if;

  -- Role resolution mirrors app.py _resolve_account_type (app.py:5961-5974):
  -- authoritative business_role, then privilege (-> vendor docs, as _legal_role),
  -- then the profile fallback. Never GoTrue metadata, which the user can rewrite.
  select business_role, role into v_business, v_priv
    from public.user_roles where user_id = p_uid;

  if v_business in ('farmer','vendor','hotel','supplier','transporter','exporter') then
    v_role := v_business;
  elsif v_priv in ('admin','superadmin') then
    v_role := 'vendor';
  elsif exists (select 1 from public.farmers where id = p_uid) then
    v_role := 'farmer';
  elsif exists (select 1 from public.transporters where supabase_uid = p_uid) then
    v_role := 'transporter';
  elsif exists (select 1 from public.buyers where id = p_uid) then
    select buyer_type into v_btype from public.buyers where id = p_uid;
    v_role := case when v_btype in ('hotel','restaurant') then 'hotel' else 'vendor' end;
  else
    -- Unresolvable role: fail closed. The Flask gate answers 409
    -- account_type_required for the same state; the database backstop refuses the
    -- write rather than guessing a role.
    return false;
  end if;

  -- Every current requirement for the role must be satisfied. An empty set (the
  -- table not yet synced, or a role with no required docs) means complete=true,
  -- which is what makes applying this migration a no-op until enforcement is on.
  for r in
    select doc_type, version, last_material_version
      from public.legal_current_requirements where role = v_role
  loop
    select exists (
      select 1 from public.legal_acceptances a
       where a.user_id = p_uid
         and a.doc_type = r.doc_type
         and (a.version = r.version
              or public._legal_ver_ge(a.version, r.last_material_version))
    ) into v_ok;
    if not v_ok then
      return false;
    end if;
  end loop;

  return true;
end;
$fn$;

revoke all on function public.legal_consent_complete(uuid) from public, anon, authenticated;
grant execute on function public.legal_consent_complete(uuid) to service_role;

-- ── 4. The trigger function every guarded table shares ───────────────────────
create or replace function public._enforce_legal_consent()
returns trigger
language plpgsql
security definer set search_path = public, pg_temp
as $fn$
begin
  -- service_role (the backend) carries no end-user JWT; anon has none either and
  -- is already refused by RLS on these tables. Only a signed-in caller is checked.
  if auth.uid() is null then
    return coalesce(new, old);
  end if;

  if not public.legal_consent_complete(auth.uid()) then
    raise exception 'AgriBridge requires acceptance of the current agreements before this action'
      using errcode = '42501',
            hint    = 'Review and accept the current agreements in Settings then try again.';
  end if;

  return coalesce(new, old);
end;
$fn$;

revoke all on function public._enforce_legal_consent() from public;

-- ── 5. Install the guard on the marketplace write paths ──────────────────────
-- drop-then-create so a re-run cannot leave two triggers, and a dashboard-renamed
-- trigger is not silently kept alongside the new one (0026's style).

drop trigger if exists legal_consent_guard on public.listings;
create trigger legal_consent_guard
  before insert or update on public.listings
  for each row execute function public._enforce_legal_consent();

drop trigger if exists legal_consent_guard on public.animal_listings;
create trigger legal_consent_guard
  before insert or update on public.animal_listings
  for each row execute function public._enforce_legal_consent();

drop trigger if exists legal_consent_guard on public.supplier_products;
create trigger legal_consent_guard
  before insert or update or delete on public.supplier_products
  for each row execute function public._enforce_legal_consent();

drop trigger if exists legal_consent_guard on public.reviews;
create trigger legal_consent_guard
  before insert or update or delete on public.reviews
  for each row execute function public._enforce_legal_consent();

drop trigger if exists legal_consent_guard on public.community_posts;
create trigger legal_consent_guard
  before insert on public.community_posts
  for each row execute function public._enforce_legal_consent();

-- orders: INSERT is only reachable through create_order_atomic (the direct INSERT
-- policy is `with check (false)`, 0002:112); UPDATE covers both a direct PATCH and
-- set_order_status. The trigger fires inside the SECURITY DEFINER RPC because
-- auth.uid() still reads the caller's JWT.
drop trigger if exists legal_consent_guard on public.orders;
create trigger legal_consent_guard
  before insert or update on public.orders
  for each row execute function public._enforce_legal_consent();

-- order_offers is created by 0004, which may not be applied on every project yet.
-- Guard accept_offer / reject_offer when it exists; skip cleanly when it does not,
-- so this file stays additive on a project that has not run the matching stage.
do $$
begin
  if exists (select 1 from information_schema.tables
              where table_schema = 'public' and table_name = 'order_offers') then
    drop trigger if exists legal_consent_guard on public.order_offers;
    create trigger legal_consent_guard
      before update on public.order_offers
      for each row execute function public._enforce_legal_consent();
    raise notice '0031: consent guard installed on public.order_offers';
  else
    raise notice '0031: public.order_offers absent (0004 not applied); offer RPC guard skipped';
  end if;
end $$;

-- ── 6. Atomic sync of the requirement set (backend only) ─────────────────────
-- One function body = one transaction: the table is never left half-populated, so
-- the guard cannot fail open on a partial sync. Replaces the whole set each call.
create or replace function public.sync_legal_current_requirements(p_rows jsonb)
returns integer
language plpgsql
security definer set search_path = public, pg_temp
as $fn$
declare
  n integer := 0;
  r jsonb;
begin
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'p_rows must be a JSON array' using errcode = '22023';
  end if;

  delete from public.legal_current_requirements;
  for r in select * from jsonb_array_elements(p_rows) loop
    insert into public.legal_current_requirements
      (role, doc_type, version, last_material_version, requires_acceptance)
    values
      (r->>'role', r->>'doc_type', r->>'version', r->>'last_material_version',
       coalesce(r->>'requires_acceptance', 'accept'));
    n := n + 1;
  end loop;
  return n;
end;
$fn$;

revoke all on function public.sync_legal_current_requirements(jsonb) from public, anon, authenticated;
grant execute on function public.sync_legal_current_requirements(jsonb) to service_role;

commit;