/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0020 (transporter assignment) against a real PostgreSQL
// engine (PGlite). docs/TRANSPORTER_PORTAL_AUDIT.md §4 records that production
// `deliveries` has no transporter linkage at all, so this file builds the table
// as base-schema.sql:85-98 declares it, reproduces the two exposures 0020 closes
// (the anonymous read, and the absence of any exclusivity rule), then proves:
// a claim lands exactly once, a second claim loses, the unique index backstops a
// direct write, rates are NOT invented when the config table is empty, and the
// rollback does not put the anonymous read back.
//
// Honesty note: PGlite is one backend, so two claims run sequentially. That
// exercises the conditional-update guard but is not wall-clock interleaving; the
// partial unique index is what makes a real concurrent race safe, and it is
// asserted separately against a direct write.
// Run:  cd tests/sql && npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const up = readFileSync(new URL('0020_transport_assignment.up.sql', dir), 'utf8');
const down = readFileSync(new URL('0020_transport_assignment.down.sql', dir), 'utf8');

const db = new PGlite();
await db.exec(`
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
  create schema auth;
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema public, auth to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;

  create table public.orders (
    id uuid primary key default gen_random_uuid(),
    buyer_id uuid, farmer_id uuid,
    status text default 'pending'
  );

  -- Verbatim columns from supabase/base-schema.sql:85-98 (the live table).
  create table public.deliveries (
    id uuid primary key default gen_random_uuid(),
    order_id uuid references public.orders(id),
    driver_name text, driver_phone text, vehicle_plate text,
    status text default 'scheduled'
      check (status in ('scheduled','picked_up','in_transit','at_checkpoint','delivered','failed')),
    pickup_location text, delivery_location text,
    estimated_arrival timestamptz, actual_arrival timestamptz,
    tracking_code text unique default upper(substr(md5(random()::text),1,8)),
    gps_lat numeric, gps_lng numeric, notes text,
    created_at timestamptz default now(), updated_at timestamptz default now()
  );

  -- Verbatim from migrations/0008_transporters_and_system_events.sql.
  create table public.transporters (
    id uuid primary key default gen_random_uuid(),
    supabase_uid uuid unique,
    full_name text not null, phone text not null, email text,
    vehicle_type text, plate_number text, district text, capacity_kg numeric,
    notes text,
    status text not null default 'active' check (status in ('active','inactive')),
    is_available boolean not null default false,
    availability_updated_at timestamptz,
    created_at timestamptz default now(), updated_at timestamptz default now()
  );

  alter table public.orders enable row level security;
  alter table public.deliveries enable row level security;
  alter table public.transporters enable row level security;

  -- Supabase grants ALL on public tables to these roles by default, which is why
  -- 0020 has to revoke rather than merely add a policy.
  grant all on public.orders to anon, authenticated, service_role;
  grant all on public.deliveries to anon, authenticated, service_role;
  grant all on public.transporters to anon, authenticated, service_role;

  -- base-schema.sql:255 — the exposure 0020 closes.
  create policy deliveries_public_select on public.deliveries
    for select to public using (true);

  -- 0008 policies, needed for the "transporter reads own deliveries" subquery to
  -- resolve the caller's own transporter id under RLS.
  create policy "transporter read own" on public.transporters
    for select using (supabase_uid = auth.uid());
  create policy "transporter update own availability" on public.transporters
    for update using (supabase_uid = auth.uid()) with check (supabase_uid = auth.uid());
  create policy "transporters service role full access" on public.transporters
    for all to service_role using (true) with check (true);
`);

let passed = 0;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql, params) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql, params); } finally { await db.exec('reset role;'); }
};
// Asserts the statement fails with a specific SQLSTATE, and returns the error.
const fails = async (code, ...a) => {
  let err = null;
  try { await as(...a); } catch (e) { err = e; }
  assert.ok(err, `expected failure ${code}, but the statement succeeded`);
  assert.equal(err.code, code, `expected ${code}, got ${err.message}`);
  return err;
};

const T1_UID = '11111111-1111-1111-1111-111111111111';
const T2_UID = '22222222-2222-2222-2222-222222222222';
const ADMIN = '99999999-9999-9999-9999-999999999999';

const svc = (sql, params) => as('service_role', null, sql, params);

// ── 1. Reproduce the pre-migration exposure exactly as production is set up ──
const ORD1 = 'a0000000-0000-0000-0000-000000000001';
const ORD2 = 'a0000000-0000-0000-0000-000000000002';
const ORD3 = 'a0000000-0000-0000-0000-000000000003';
// ORD4/ORD5 carry no delivery of their own: section 6 needs orders that are not
// already live-assigned, otherwise the insert it is testing would collide with a
// claim made earlier in this same file and prove nothing.
const ORD4 = 'a0000000-0000-0000-0000-000000000004';
const ORD5 = 'a0000000-0000-0000-0000-000000000005';
await svc(`insert into public.orders (id) values
  ('${ORD1}'),('${ORD2}'),('${ORD3}'),('${ORD4}'),('${ORD5}')`);
const TR1 = 'b0000000-0000-0000-0000-000000000001';
const TR2 = 'b0000000-0000-0000-0000-000000000002';
const TR_INACTIVE = 'b0000000-0000-0000-0000-000000000003';
await svc(`insert into public.transporters (id, supabase_uid, full_name, phone, vehicle_type) values
  ('${TR1}','${T1_UID}','A Driver','0700000001','pickup'),
  ('${TR2}','${T2_UID}','B Driver','0700000002','lorry'),
  ('${TR_INACTIVE}',null,'C Retired','0700000003','van')`);
await svc(`update public.transporters set status='inactive' where id='${TR_INACTIVE}'`);

const D1 = 'c0000000-0000-0000-0000-000000000001';
const D2 = 'c0000000-0000-0000-0000-000000000002';
const D3 = 'c0000000-0000-0000-0000-000000000003';
await svc(`insert into public.deliveries (id, order_id, driver_name, driver_phone, vehicle_plate, gps_lat, gps_lng, notes) values
  ('${D1}','${ORD1}','A Driver','0700000001','UAA111A',0.3476,32.5825,'Nakawa to Gulu'),
  ('${D2}','${ORD2}','B Driver','0700000002','UBB222B',0.6663,31.4,'Kampala to Mbale'),
  ('${D3}','${ORD3}',null,null,null,null,null,null)`);

let r = await as('anon', null,
  `select driver_phone, vehicle_plate, gps_lat from public.deliveries where id='${D1}'`);
assert.equal(r.rows[0].driver_phone, '0700000001');
ok('before 0020 an anonymous caller reads driver phone, plate and GPS from deliveries');

// The live probe in docs/TRANSPORTER_PORTAL_AUDIT.md §4 returned 42703 for this
// column; reproduce it here so the migration is tested against the real gap.
await fails('42703', 'service_role', null,
  'select transporter_id from public.deliveries');
ok('pre-0020 there is no linkage column, so nothing can express "this job is taken"');

// ── 2. Apply the fix ──────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0020 applies and re-applies idempotently');

r = await as('anon', null, `select id from public.deliveries`);
assert.equal(r.rows.length, 0);
ok('after 0020 an anonymous caller reads no delivery rows at all');

r = await svc('select count(*)::int c from public.deliveries');
assert.equal(r.rows[0].c, 3);
ok('0020 rewrote nothing: the pre-existing rows and their columns survive');

// The client cannot write the table directly any more, at any privilege level.
await fails('42501', 'authenticated', T1_UID,
  `update public.deliveries set status='delivered' where id='${D1}'`);
ok('a signed-in transporter cannot write deliveries directly through PostgREST');
await fails('42501', 'anon', null,
  `insert into public.deliveries (order_id) values ('${ORD3}')`);
ok('anon cannot insert into deliveries');

r = await svc(`update public.deliveries set notes='admin path' where id='${D3}' returning notes`);
assert.equal(r.rows[0].notes, 'admin path');
ok('the service-role backend keeps full write access, so admin assignment still works');

// ── 3. A transporter reads only their own jobs ────────────────────────────────
await svc(`update public.deliveries set transporter_id='${TR1}', assigned_by='claim',
                   assigned_at=now() where id='${D1}'`);
r = await as('authenticated', T1_UID, `select id from public.deliveries`);
assert.deepEqual(r.rows.map((x) => x.id), [D1]);
ok('the assigned transporter sees exactly their own delivery');

r = await as('authenticated', T2_UID, `select id from public.deliveries`);
assert.equal(r.rows.length, 0);
ok('another signed-in transporter sees nothing, including the job they did not take');

// ── 4. Rates are configuration, not an invented price ─────────────────────────
r = await svc(`select public.transport_quote('pickup', 40, 250, false) q`);
assert.equal(r.rows[0].q.status, 'not_configured');
assert.ok(!('total' in r.rows[0].q) && !('base_fee' in r.rows[0].q));
assert.match(r.rows[0].q.message, /administrator must enter/i);
ok('transport_quote returns not_configured against the empty rate table, never a number');

await fails('42501', 'authenticated', T1_UID, `select public.transport_quote('pickup',40,250,false)`);
ok('a client role cannot call transport_quote; only the backend can');

r = await svc('select count(*)::int c from public.transport_rate_config');
assert.equal(r.rows[0].c, 0);
ok('the rate table ships empty, so no fee is quoted from a seeded assumption');

await svc(`insert into public.transport_rate_config
  (vehicle_type, base_fee_ugx, ugx_per_km, ugx_per_kg, night_surcharge_pct)
  values ('pickup', 20000, 500, 20, 10)`);
r = await svc(`select public.transport_quote('pickup', 40, 250, false) q`);
const q = r.rows[0].q;
assert.equal(q.status, 'ok');
assert.equal(Number(q.distance_component), 20000);
assert.equal(Number(q.weight_component), 5000);
assert.equal(Number(q.night_surcharge), 0);
assert.equal(Number(q.rates_snapshot.ugx_per_km), 500);
assert.equal(Number(q.total), 45000);
ok('once an operator records rates, the quote is arithmetic over those rates');

r = await svc(`select public.transport_quote('pickup', 40, 250, true) q`);
assert.equal(Number(r.rows[0].q.night_surcharge), 4500);
assert.equal(Number(r.rows[0].q.total), 49500);
ok('the night surcharge is a percentage of the configured components, not a guess');

r = await svc(`select public.transport_quote('lorry', 40, 250, false) q`);
assert.equal(r.rows[0].q.status, 'not_configured');
ok('a vehicle type with no configured rate still returns not_configured');

await fails('42501', 'anon', null,
  `select 1 from public.transport_rate_config`);
ok('rate configuration is not readable by an anonymous caller');

// ── 5. claim_delivery: exclusivity ────────────────────────────────────────────
await fails('42501', 'authenticated', T1_UID,
  `select public.claim_delivery('${D2}'::uuid, '${TR1}'::uuid, null)`);
ok('a transporter cannot call claim_delivery with a self-supplied id; the RPC is service-role only');

r = await svc(`select public.claim_delivery('${D3}'::uuid, '${TR1}'::uuid, 45000) q`);
assert.equal(r.rows[0].q.claimable, true);
r = await svc(`select transporter_id, assigned_by, transport_fee_ugx, fee_basis,
                      cast(is_available as text) av
               from public.deliveries d
               join public.transporters t on t.id = d.transporter_id
               where d.id='${D3}'`);
assert.equal(r.rows[0].transporter_id, TR1);
assert.equal(r.rows[0].assigned_by, 'claim');
assert.equal(Number(r.rows[0].transport_fee_ugx), 45000);
assert.equal(r.rows[0].fee_basis, 'admin_set');
assert.equal(r.rows[0].av, 'false');
ok('a successful claim records the fee, marks it as claimed and occupies the driver');

r = await svc(`select public.claim_delivery('${D3}'::uuid, '${TR2}'::uuid, 50000) q`);
assert.equal(r.rows[0].q.claimable, false);
assert.equal(r.rows[0].q.reason, 'already_claimed');
r = await svc(`select transporter_id, transport_fee_ugx from public.deliveries where id='${D3}'`);
assert.equal(r.rows[0].transporter_id, TR1);
assert.equal(Number(r.rows[0].transport_fee_ugx), 45000);
ok('the second claim loses and does not overwrite the winner or the agreed fee');

const [bothA, bothB] = await Promise.all([
  svc(`select public.claim_delivery('${D2}'::uuid, '${TR2}'::uuid, null) q`),
  svc(`select public.claim_delivery('${D2}'::uuid, '${TR1}'::uuid, null) q`),
]);
const wins = [bothA, bothB].filter((x) => x.rows[0].q.claimable).length;
assert.equal(wins, 1);
ok('two claims fired together leave exactly one winner');

r = await svc(`select public.claim_delivery('${D1}'::uuid, '${TR2}'::uuid, null) q`);
assert.equal(r.rows[0].q.reason, 'already_claimed');
ok('a re-claim of a job already held by someone else is refused, not silently reassigned');

r = await svc(`select public.claim_delivery('${D1}'::uuid, '${TR_INACTIVE}'::uuid, null) q`);
assert.equal(r.rows[0].q.claimable, false);
assert.equal(r.rows[0].q.reason, 'transporter_inactive');
ok('an inactive transporter cannot claim, so admin deactivation has teeth');

r = await svc(`select public.claim_delivery('${D1}'::uuid, 'd0000000-0000-0000-0000-000000000099'::uuid, null) q`);
assert.equal(r.rows[0].q.reason, 'transporter_not_found');
ok('a transporter id that does not exist is refused rather than trusted');

r = await svc(`select public.claim_delivery('d0000000-0000-0000-0000-000000000099'::uuid, '${TR2}'::uuid, null) q`);
assert.equal(r.rows[0].q.reason, 'delivery_not_found');
ok('a delivery id that does not exist is refused');

// A completed job must not be re-openable by a claim. Release the linkage first:
// with transporter_id still set, claim_delivery reports already_claimed, and that
// branch is asserted above — this one needs the status guard to be reached.
await svc(`update public.deliveries set status='delivered', transporter_id=null
           where id='${D3}'`);
r = await svc(`select public.claim_delivery('${D3}'::uuid, '${TR2}'::uuid, null) q`);
assert.equal(r.rows[0].q.reason, 'not_open');
ok('a delivered job cannot be claimed');

// ── 6. The index backstop: exclusivity holds even without the function ───────
await fails('23505', 'service_role', null,
  `insert into public.deliveries (order_id, transporter_id, status)
   values ('${ORD1}', '${TR2}', 'scheduled')`);
ok('a direct write of a second live assignment for the same order is rejected by the index');

r = await svc(`insert into public.deliveries (order_id, transporter_id, status)
  values ('${ORD4}', '${TR2}', 'scheduled') returning id`);
assert.equal(r.rows.length, 1);
ok('one transporter may hold live jobs on different orders');

r = await svc(`insert into public.deliveries (order_id, transporter_id, status)
  values ('${ORD5}', '${TR2}', 'in_transit') returning id`);
assert.equal(r.rows.length, 1);
ok('a second live status for another order is still allowed, so the index is per order');

const retried = 'c0000000-0000-0000-0000-000000000009';
r = await svc(`insert into public.deliveries (id, order_id, transporter_id, status)
  values ('${retried}', '${ORD1}', '${TR_INACTIVE}', 'failed') returning id`);
assert.equal(r.rows.length, 1);
ok('a failed or delivered row does not block a retry of the same order');

await fails('42501', 'authenticated', T1_UID,
  `insert into public.deliveries (order_id, transporter_id) values ('${ORD3}','${TR1}')`);
ok('a client cannot even attempt the index-backstopped write');

// Fee and offer columns are constrained, not free-form.
await fails('23514', 'service_role', null,
  `update public.deliveries set transport_fee_ugx = -1 where id='${D1}'`);
ok('a negative transport fee is refused by the CHECK, not by the UI');
await fails('23514', 'service_role', null,
  `update public.deliveries set offer_status='maybe' where id='${D1}'`);
ok('an invented offer status is refused');

r = await svc(`select fee_disclosed_to_buyer, offer_status, transport_fee_ugx
               from public.deliveries where id='${D2}'`);
assert.equal(r.rows[0].fee_disclosed_to_buyer, false);
assert.equal(r.rows[0].offer_status, 'none');
assert.equal(r.rows[0].transport_fee_ugx, null);
ok('a job with no recorded fee reads as null + undisclosed, never as zero or agreed');

// ── 7. Rollback: honest about what it will not undo ───────────────────────────
await db.exec(down);
await db.exec(down);
ok('the down migration re-runs cleanly');

await fails('42703', 'service_role', null, 'select transporter_id from public.deliveries');
await fails('42703', 'service_role', null, 'select transport_fee_ugx from public.deliveries');
r = await svc('select count(*)::int c from public.deliveries');
assert.equal(r.rows[0].c, 6);
ok('rollback removes the columns and keeps every delivery row');

r = await as('anon', null, `select id from public.deliveries`);
assert.equal(r.rows.length, 0);
ok('rollback does NOT restore the anonymous read — the exposure stays closed');

await fails('42501', 'authenticated', T1_UID,
  `update public.deliveries set notes='should stay locked' where id='${D1}'`);
ok('rollback does NOT hand client write access back either');

await db.exec(up);
r = await svc(`select public.claim_delivery('${D2}'::uuid, '${TR2}'::uuid, null) q`);
assert.equal(r.rows[0].q.claimable, true);
ok('re-applying 0020 after a rollback restores the whole mechanism');

// ── 8. Migration 0022: the profile columns a row policy never protected ──────
const up22 = readFileSync(new URL('0022_lock_transporter_self_writes.up.sql', dir), 'utf8');
const down22 = readFileSync(new URL('0022_lock_transporter_self_writes.down.sql', dir), 'utf8');

// Deactivate driver A through the backend path, then have that driver try to
// undo it. 0014:47-50's policy is row-scoped, so this succeeds pre-0022.
await svc(`update public.transporters set status='inactive' where id='${TR1}'`);
r = await as('authenticated', T1_UID,
  `update public.transporters set status='active' where id='${TR1}' returning status`);
assert.equal(r.rows[0].status, 'active');
ok('before 0022 a transporter can undo an admin deactivation with one PATCH');

// A brand-new uid, because TR2 already owns T2_UID and supabase_uid is UNIQUE.
r = await as('authenticated', T1_UID,
  `update public.transporters set vehicle_type='lorry', capacity_kg=9000
     where id='${TR1}' returning vehicle_type, capacity_kg`);
assert.equal(r.rows[0].vehicle_type, 'lorry');
assert.equal(Number(r.rows[0].capacity_kg), 9000);
ok('pre-0022 a transporter can relabel their vehicle and claim any load capacity');
const GHOST_UID = '77777777-7777-7777-7777-777777777777';
// The one column 0014 did guard: with_check re-evaluates supabase_uid on the NEW
// row, so re-pointing the linkage is already refused. Recorded here so 0022's
// header is not credited with something it did not change.
await fails('42501', 'authenticated', T1_UID,
  `update public.transporters set supabase_uid='${GHOST_UID}' where id='${TR1}'`);
ok('re-pointing the account linkage was already refused by with_check, before and after 0022');

r = await svc(`select public.claim_delivery('${D2}'::uuid, '${TR1}'::uuid, null) q`);
assert.ok(r.rows[0].q.claimable === true || r.rows[0].q.reason === 'already_claimed');
ok('having reactivated themselves, they can immediately claim work again');

await db.exec(up22);
await db.exec(up22);
ok('0022 applies and re-applies idempotently');

await svc(`update public.transporters set status='inactive' where id='${TR1}'`);
await fails('42501', 'authenticated', T1_UID,
  `update public.transporters set status='active' where id='${TR1}'`);
r = await svc(`select status from public.transporters where id='${TR1}'`);
assert.equal(r.rows[0].status, 'inactive');
ok('after 0022 the deactivation stands: self-reactivation is refused');

await fails('42501', 'authenticated', T1_UID,
  `update public.transporters set capacity_kg=9000 where id='${TR1}'`);
await fails('42501', 'authenticated', T1_UID,
  `update public.transporters set is_available=true where id='${TR1}'`);
ok('capacity and the availability flag are no longer client-writable either');

r = await svc(`select public.claim_delivery('${D2}'::uuid, '${TR1}'::uuid, null) q`);
assert.equal(r.rows[0].q.reason, 'transporter_inactive');
ok('0020 is now protected by the lock, not just by an admin setting someone knows about');

// Reads and the backend write path must survive, or the portal breaks.
r = await as('authenticated', T1_UID, `select id, full_name from public.transporters`);
assert.deepEqual(r.rows.map((x) => x.id), [TR1]);
ok('a transporter still reads exactly their own account row');

r = await svc(`update public.transporters set is_available=true, availability_updated_at=now()
               where id='${TR1}' returning is_available`);
assert.equal(r.rows[0].is_available, true);
ok('POST /api/transporter/availability keeps working, because the backend holds the service key');

await db.exec(down22);
r = await as('authenticated', T1_UID,
  `update public.transporters set status='active' where id='${TR1}' returning status`);
assert.equal(r.rows[0].status, 'active');
ok('the down migration restores 0014:47-50 exactly as it was written, hole and all');

// ── 9. Migration 0023: proof of delivery decided under one row lock ─────────
const up23 = readFileSync(new URL('0023_submit_delivery_proof.up.sql', dir), 'utf8');
const down23 = readFileSync(new URL('0023_submit_delivery_proof.down.sql', dir), 'utf8');
// sha256('123456') — a fixed digest so this file never needs crypto at test time.
const CODE_HASH = '8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92';
const O9 = 'aa000000-0000-0000-0000-00000000000a';
const O10 = 'aa000000-0000-0000-0000-00000000000b';
const D9 = 'aa000000-0000-0000-0000-0000000000d1';
const D10 = 'aa000000-0000-0000-0000-0000000000d2';

await svc(`insert into public.orders (id) values ('${O9}'), ('${O10}')`);
await svc(`insert into public.deliveries (id, order_id, status, transporter_id, assigned_at, assigned_by)
           values ('${D9}', '${O9}', 'in_transit', '${TR1}', now(), 'admin'),
                  ('${D10}', '${O10}', 'scheduled', '${TR1}', now(), 'admin')`);

await db.exec(up23);
await db.exec(up23);
ok('0023 applies and re-applies idempotently');

await fails('42501', 'authenticated', T1_UID,
  `select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid, '${CODE_HASH}', 'x.jpg')`);
ok('a client that reached PostgREST directly still cannot call the proof function');

let r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid,
                    '${CODE_HASH}', 'proof/not-issued.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'code_not_issued');
let row9 = await svc(`select proof_submitted_at from public.deliveries where id='${D9}'`);
assert.equal(row9.rows[0].proof_submitted_at, null);
ok('no code issued: the submission is refused and nothing is recorded');

r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR2}'::uuid,
                '${CODE_HASH}', 'proof/someone-else.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'not_yours');
ok('another transporter cannot submit proof for a job they do not hold');

await svc(`update public.deliveries set proof_code_hash='${CODE_HASH}',
           proof_code_issued_at=now() where id='${D9}'`);

const WRONG = '0000000000000000000000000000000000000000000000000000000000000000';
r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid,
                '${WRONG}', 'proof/wrong.jpg') q`);
assert.equal(r9.rows[0].q.ok, true);
assert.equal(r9.rows[0].q.code_matched, false);
assert.equal(Number(r9.rows[0].q.attempts_remaining), 4);
row9 = await svc(`select proof_code_matches, proof_code_attempts, proof_photo_path
                  from public.deliveries where id='${D9}'`);
assert.equal(row9.rows[0].proof_code_matches, false);
assert.equal(Number(row9.rows[0].proof_code_attempts), 1);
assert.equal(row9.rows[0].proof_photo_path, 'proof/wrong.jpg');
ok('a wrong code is recorded as a failed attempt, and the photograph is kept for review');

r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid,
                '${CODE_HASH}', 'proof/right.jpg') q`);
assert.equal(r9.rows[0].q.code_matched, true);
row9 = await svc(`select proof_code_matches, proof_code_attempts, proof_photo_path,
                  proof_submitted_at from public.deliveries where id='${D9}'`);
assert.equal(row9.rows[0].proof_code_matches, true);
assert.equal(Number(row9.rows[0].proof_code_attempts), 1);
assert.equal(row9.rows[0].proof_photo_path, 'proof/right.jpg');
assert.ok(row9.rows[0].proof_submitted_at);
ok('the matching code stores the photograph and does not spend a failed attempt');

r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid,
                '${CODE_HASH}', 'proof/overwrite.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'already_submitted');
row9 = await svc(`select proof_photo_path from public.deliveries where id='${D9}'`);
assert.equal(row9.rows[0].proof_photo_path, 'proof/right.jpg');
ok('a second submission cannot overwrite the evidence already on record');

await svc(`update public.deliveries set proof_code_hash='${CODE_HASH}' where id='${D10}'`);
for (let i = 0; i < 5; i += 1) {
  r9 = await svc(`select public.submit_delivery_proof('${D10}'::uuid, '${TR1}'::uuid,
                  '${WRONG}', 'proof/guess.jpg') q`);
}
assert.equal(Number(r9.rows[0].q.attempts_remaining), 0);
r9 = await svc(`select public.submit_delivery_proof('${D10}'::uuid, '${TR1}'::uuid,
                '${CODE_HASH}', 'proof/late.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'locked_out');
ok('five wrong guesses lock the code out, even for the person who holds the goods');

await svc(`update public.deliveries set status='delivered' where id='${D10}'`);
r9 = await svc(`select public.submit_delivery_proof('${D10}'::uuid, '${TR1}'::uuid,
                '${CODE_HASH}', 'proof/after.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'closed');
ok('a closed delivery accepts no new proof');

await db.exec(down23);
await fails('42883', 'service_role', null,
  `select public.submit_delivery_proof('${D9}'::uuid, '${TR1}'::uuid, '${CODE_HASH}', 'x.jpg')`);
row9 = await svc(`select proof_photo_path from public.deliveries where id='${D9}'`);
assert.equal(row9.rows[0].proof_photo_path, 'proof/right.jpg');
ok('the rollback removes the write path and leaves the recorded evidence intact');

await db.exec(up23);
r9 = await svc(`select public.submit_delivery_proof('${D9}'::uuid, '${TR2}'::uuid,
                '${CODE_HASH}', 'proof/again.jpg') q`);
assert.equal(r9.rows[0].q.reason, 'not_yours');
ok('re-applying 0023 after a rollback restores the same refusals');

console.log(`\n${passed} transport-assignment assertions passed`);
