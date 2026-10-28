-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0020 — A delivery can finally answer "whose job is this, for how much, and how
-- do we know it arrived?"
--
-- Why this migration exists at all: docs/TRANSPORTER_PORTAL_AUDIT.md §4 records
-- that `deliveries` has no transporter linkage. It stores driver_name,
-- driver_phone and vehicle_plate as free text (base-schema.sql:88-90) and the
-- string `transporter_id` appears in no .sql file in this repository. Verified
-- against the live project on 2026-10-27, not inferred:
--
--   GET /rest/v1/deliveries?select=transporter_id  -> 400 42703 "column does not exist"
--   GET /rest/v1/deliveries?select=id&limit=0      -> 200 content-range: */0
--
-- The second line is the reason the portal could not be built first: an
-- assignment is not merely unmodelled, `deliveries` holds zero rows, so there is
-- no legacy data to migrate or reconcile here. app.py:3476 already filters
-- deliveries on transporter_id, which is why POST /api/transporter/location
-- cannot succeed in production and returns its own 503 path.
--
-- Three deliberate design choices:
--
-- 1. No price is invented. transport_fee_ugx is nullable with no default, and
--    transport_rate_config is created EMPTY. An empty rate table means "not
--    configured", and the quote function must return that rather than a number.
--    The mandate forbids inventing commissions or deductions, so the calculator
--    ships with inputs and no seeded assumptions; an operator supplies the
--    figures. Nothing here implies a fee exists until someone records one.
--
-- 2. The recipient code is stored hashed. If the expected code sat in a plain
--    column that the assigned transporter can read, the transporter could
--    complete a delivery without ever meeting the buyer, which is worse than the
--    status quo. proof_code_hash is written by the backend at assignment and only
--    ever compared, never returned.
--
-- 3. Proof photographs do not go in the existing `uploads` bucket. 0012:9-16
--    creates it with public = true and 0012:29-33 grants storage.objects select
--    to public, so any object placed there is world-readable by URL. A proof of
--    delivery is a customer's property and address; it needs its own private
--    bucket, which is why this migration only stores an object *path* and the
--    bucket itself is created by 0021_proof_of_delivery_bucket.up.sql.
--
-- Exclusivity is enforced twice on purpose: claim_delivery() does a conditional
-- update that can only land once, and a partial unique index makes a second live
-- assignment for the same order impossible even if a future caller forgets the
-- function. The index is the backstop, not the mechanism.
--
-- Adds columns, one table, two functions, and replaces policies. It touches no
-- existing rows (there are none) and drops no data. Safe to re-run.
--
-- Two grant lines here look redundant and are not. Supabase normally leaves a
-- new table executable/readable by service_role through default privileges, and
-- this migration cannot assume that: PGlite reproduced "permission denied for
-- table transport_rate_config" from the service role, and an INVOKER
-- transport_quote() failed the same way for the backend that owns quoting. Both
-- functions are therefore security definer and service_role is granted the rate
-- table explicitly. Verified by tests/sql/transport_assignment.test.mjs.

begin;

-- ── 1. Linkage ───────────────────────────────────────────────────────────────
-- Nullable by design: an unassigned job must remain representable, and existing
-- rows written by admin_assign_transporter (app.py:3905-3918) carry only the
-- free-text driver fields, so they stay valid with transporter_id null.
alter table public.deliveries add column if not exists transporter_id uuid
  references public.transporters(id) on delete set null;
alter table public.deliveries add column if not exists assigned_at timestamptz;
alter table public.deliveries add column if not exists assigned_by text
  check (assigned_by in ('admin', 'claim'));

create index if not exists deliveries_transporter_idx
  on public.deliveries (transporter_id, status);
create index if not exists deliveries_unassigned_idx
  on public.deliveries (order_id) where transporter_id is null;

-- One live assignment per order. 'delivered' and 'failed' are excluded so a
-- retried job is possible; a cancelled order's delivery row is released by
-- setting transporter_id null, not by deleting the row.
create unique index if not exists deliveries_one_live_assignment_idx
  on public.deliveries (order_id)
  where transporter_id is not null
    and status in ('scheduled', 'picked_up', 'in_transit', 'at_checkpoint');

-- ── 2. Money ─────────────────────────────────────────────────────────────────
-- The agreed fee, in UGX. NULL means "not recorded", which is the honest state
-- for every delivery that exists today.
alter table public.deliveries add column if not exists transport_fee_ugx numeric
  check (transport_fee_ugx is null or transport_fee_ugx >= 0);
alter table public.deliveries add column if not exists fee_basis text
  check (fee_basis in ('admin_set', 'calculated', 'negotiated'));
-- Snapshot of the inputs behind a calculated quote, so a fee can be explained
-- later without recomputing it from rates that may have changed. The browser
-- never recalculates financial truth (mandate: no financial truth in the client).
alter table public.deliveries add column if not exists fee_quote_inputs jsonb;
alter table public.deliveries add column if not exists fee_quoted_at timestamptz;
-- Only an admin discloses a figure to the buyer; the portal shows the
-- transporter their own agreed fee regardless.
alter table public.deliveries add column if not exists fee_disclosed_to_buyer boolean
  not null default false;

-- Transporter counter-offer. A proposal is not an agreement and is never
-- displayed as money owed.
alter table public.deliveries add column if not exists transporter_offer_ugx numeric
  check (transporter_offer_ugx is null or transporter_offer_ugx >= 0);
alter table public.deliveries add column if not exists offer_status text
  not null default 'none'
  check (offer_status in ('none', 'proposed', 'accepted', 'rejected', 'withdrawn'));
alter table public.deliveries add column if not exists offer_at timestamptz;
alter table public.deliveries add column if not exists offer_decided_at timestamptz;

-- ── 3. Proof of delivery ─────────────────────────────────────────────────────
-- Recipient code: hashed at issue, compared on submit, never selected.
alter table public.deliveries add column if not exists proof_code_hash text;
alter table public.deliveries add column if not exists proof_code_issued_at timestamptz;
alter table public.deliveries add column if not exists proof_code_matches boolean;
-- Wrong guesses are counted and capped (5), so a 6-digit code cannot be walked
-- through by the one person who benefits from guessing it: the driver holding
-- the goods. 0023_submit_delivery_proof.up.sql increments this under a row lock.
alter table public.deliveries add column if not exists proof_code_attempts integer
  not null default 0 check (proof_code_attempts >= 0);
-- Private-bucket object path, not a public URL (see header note 3).
alter table public.deliveries add column if not exists proof_photo_path text;
alter table public.deliveries add column if not exists proof_submitted_at timestamptz;
-- Admin confirmation stays required: a button press is not a delivery.
-- text, not uuid: the legacy admin JWT's subject is an opaque string ('admin'),
-- and recording who confirmed must not fail because it is not a uuid.
alter table public.deliveries add column if not exists proof_verified_by text;
alter table public.deliveries add column if not exists proof_verified_at timestamptz;

-- ── 4. Rate configuration, intentionally empty ───────────────────────────────
-- One row per vehicle type. No INSERT here on purpose: seeding a fuel rate would
-- be inventing a price. transport_quote() returns status 'not_configured' until
-- an operator writes real figures, and the UI must show that, not a guess.
create table if not exists public.transport_rate_config (
  vehicle_type    text primary key,
  base_fee_ugx    numeric not null check (base_fee_ugx >= 0),
  ugx_per_km      numeric not null check (ugx_per_km >= 0),
  ugx_per_kg      numeric not null check (ugx_per_kg >= 0),
  night_surcharge_pct numeric not null default 0 check (night_surcharge_pct between 0 and 100),
  note            text,
  -- text for the same reason proof_verified_by is: the admin console's identity
  -- is the legacy JWT subject ('admin'), which is not a uuid.
  updated_by      text,
  updated_at      timestamptz default now()
);
alter table public.transport_rate_config enable row level security;
drop policy if exists "transport rates service role only" on public.transport_rate_config;
create policy "transport rates service role only" on public.transport_rate_config
  for all to service_role using (true) with check (true);
revoke all on table public.transport_rate_config from public, anon, authenticated;
-- The revoke above is not enough on its own. Supabase's default privileges are
-- what usually leave service_role able to read a new table, and this migration
-- cannot assume they exist: PGlite reproduced "permission denied for table
-- transport_rate_config" from the service role before this line was added. The
-- backend reads rates through transport_quote() and writes them from the admin
-- panel, so it needs the table itself, and nothing else does.
grant all on table public.transport_rate_config to service_role;

-- ── 5. Quote helper ──────────────────────────────────────────────────────────
-- Pure arithmetic over stored rates. Returns not_configured rather than a
-- plausible-looking zero, because a zero would be read as "free" by a driver.
--
-- security definer is load-bearing, not decoration: this function reads
-- transport_rate_config, which the revoke below takes away from every client
-- role. As a plain INVOKER function the backend's own call would fail with
-- 42501 "permission denied for table transport_rate_config" — which
-- tests/sql/transport_assignment.test.mjs reproduced against PGlite before this
-- line was added. Definer means the caller needs EXECUTE and nothing else, and
-- EXECUTE is held only by service_role.
create or replace function public.transport_quote(
  p_vehicle_type text,
  p_distance_km  numeric,
  p_weight_kg    numeric,
  p_night        boolean default false
) returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  r record;
begin
  select * into r from public.transport_rate_config where vehicle_type = p_vehicle_type;
  if not found then
    return jsonb_build_object('status', 'not_configured',
                              'message', 'No transport rates are configured for '
                                         || coalesce(p_vehicle_type, 'this vehicle')
                                         || '. An administrator must enter them before a fee can be quoted.');
  end if;
  return jsonb_build_object(
    'status', 'ok',
    'currency', 'UGX',
    'base_fee', r.base_fee_ugx,
    'distance_component', round(coalesce(p_distance_km, 0) * r.ugx_per_km, 0),
    'weight_component', round(coalesce(p_weight_kg, 0) * r.ugx_per_kg, 0),
    'night_surcharge', case when coalesce(p_night, false) then
          round((r.base_fee_ugx + coalesce(p_distance_km, 0) * r.ugx_per_km
                 + coalesce(p_weight_kg, 0) * r.ugx_per_kg)
                * r.night_surcharge_pct / 100, 0) else 0 end,
    'total', round(r.base_fee_ugx
                   + coalesce(p_distance_km, 0) * r.ugx_per_km
                   + coalesce(p_weight_kg, 0) * r.ugx_per_kg
                   + case when coalesce(p_night, false) then
                       (r.base_fee_ugx + coalesce(p_distance_km, 0) * r.ugx_per_km
                        + coalesce(p_weight_kg, 0) * r.ugx_per_kg)
                        * r.night_surcharge_pct / 100 else 0 end, 0),
    'inputs', jsonb_build_object('vehicle_type', p_vehicle_type,
                                 'distance_km', p_distance_km,
                                 'weight_kg', p_weight_kg,
                                 'night', p_night),
    'rates_snapshot', jsonb_build_object('base_fee_ugx', r.base_fee_ugx,
                                         'ugx_per_km', r.ugx_per_km,
                                         'ugx_per_kg', r.ugx_per_kg,
                                         'night_surcharge_pct', r.night_surcharge_pct)
  );
end;
$$;
revoke all on function public.transport_quote(text, numeric, numeric, boolean)
  from public, anon, authenticated;
-- The revoke above also takes away the grant PUBLIC gives by default, and
-- service_role has no privilege of its own, so without this line the backend
-- that owns quoting would be unable to call its own calculator.
grant execute on function public.transport_quote(text, numeric, numeric, boolean)
  to service_role;

-- ── 6. Atomic claim ──────────────────────────────────────────────────────────
-- Called by the backend with the service role, after it has verified the
-- Supabase session and resolved the caller's own transporter row. It never
-- accepts a transporter id from the browser. The update is conditional on the
-- row still being unassigned, so two simultaneous claims cannot both land; the
-- loser gets claimable = false and the UI refreshes the list.
create or replace function public.claim_delivery(
  p_delivery_id    uuid,
  p_transporter_id uuid,
  p_fee_ugx        numeric default null
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  r public.deliveries%rowtype;
  t public.transporters%rowtype;
begin
  select * into t from public.transporters where id = p_transporter_id;
  if not found then
    return jsonb_build_object('claimable', false, 'reason', 'transporter_not_found');
  end if;
  if t.status <> 'active' then
    return jsonb_build_object('claimable', false, 'reason', 'transporter_inactive');
  end if;

  select * into r from public.deliveries where id = p_delivery_id for update;
  if not found then
    return jsonb_build_object('claimable', false, 'reason', 'delivery_not_found');
  end if;
  if r.transporter_id is not null then
    return jsonb_build_object('claimable', false, 'reason', 'already_claimed',
                              'status', r.status);
  end if;
  if r.status not in ('scheduled') then
    return jsonb_build_object('claimable', false, 'reason', 'not_open',
                              'status', r.status);
  end if;

  update public.deliveries
     set transporter_id = p_transporter_id,
         assigned_at    = now(),
         assigned_by    = 'claim',
         transport_fee_ugx = p_fee_ugx,
         fee_basis      = case when p_fee_ugx is null then null else 'admin_set' end,
         updated_at     = now()
   where id = p_delivery_id
     and transporter_id is null;

  if not found then
    return jsonb_build_object('claimable', false, 'reason', 'already_claimed');
  end if;

  -- A claim is not availability: the driver is now occupied.
  update public.transporters
     set is_available = false, availability_updated_at = now()
   where id = p_transporter_id;

  return jsonb_build_object('claimable', true, 'delivery_id', p_delivery_id,
                            'transporter_id', p_transporter_id,
                            'transport_fee_ugx', p_fee_ugx);
end;
$$;
revoke all on function public.claim_delivery(uuid, uuid, numeric)
  from public, anon, authenticated;
grant execute on function public.claim_delivery(uuid, uuid, numeric) to service_role;

-- ── 7. Row-level security ────────────────────────────────────────────────────
-- base-schema.sql:255 created
--   create policy deliveries_public_select on public.deliveries for select to
--     public using (true)
-- which lets an anonymous caller read every delivery row, including driver_phone,
-- vehicle_plate and gps_lat/gps_lng. Confirmed live on 2026-10-27 (the query
-- returned 200; only the absence of rows has been protecting it). Tracking does
-- not depend on it: GET /api/track/<code> (app.py:4191) reads through the
-- service-role backend key, so removing the policy changes nothing for buyers.
drop policy if exists deliveries_public_select on public.deliveries;

drop policy if exists "deliveries service role full access" on public.deliveries;
create policy "deliveries service role full access" on public.deliveries
  for all to service_role using (true) with check (true);

-- Defence in depth for a future direct-PostgREST mobile client: a transporter may
-- read their own jobs and nothing else. Every write still goes through the
-- backend, because RLS predicates are row-scoped and cannot protect the fee or
-- proof columns from the row's own owner.
drop policy if exists "transporter reads own deliveries" on public.deliveries;
create policy "transporter reads own deliveries" on public.deliveries
  for select to authenticated
  using (transporter_id in (select id from public.transporters
                            where supabase_uid = auth.uid()));

-- No client may write deliveries directly. This is the same class of hole 0019
-- closed on orders: an unguarded client write path into a table whose status
-- feeds a financial view.
drop policy if exists "deliveries client update" on public.deliveries;
revoke update, insert, delete, truncate on public.deliveries from public, anon, authenticated;
grant select on public.deliveries to authenticated;

commit;
