/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0029 (exporter role + international phones) against a real
// PostgreSQL engine, on top of the same chain the live project must walk:
// 0025 → 0026 → 0028 → 0029.
//
// The offer that prompted this: Five Trading & Supplies (Egypt, +20 103 679 7717)
// wants to sell onions through AgriBridge. Before 0029 the database refuses their
// number (0025's +256-only CHECK) and has no word for what they are
// (0028's business_role CHECK). The owner chose a new "Exporter / Trading Co."
// type over filing a foreign company under 'farmer'.
//
// Measured here:
//   1. the hole is open before 0029 and shut after it — an Egyptian number is
//      refused by farmers_phone_uganda, then stored by farmers_phone_e164;
//   2. 'exporter' is a storable business role, and the words that are NOT roles
//      stay refused;
//   3. an exporter is still ONE role: their farmers row (sellers live in farmers,
//      listings.farmer_id points there) trips 0028's trigger against buyers;
//   4. transporters keep the +256 rule — drivers are local, admin-created;
//   5. update_my_profile validates the same E.164 shape and 0025's name list;
//   6. the rollback restores the old rules without touching rows, and a foreign
//      number written under 0029 then fails its next UPDATE (the NOT VALID
//      pressure 0025 documented, in reverse).
//
// Run:  cd tests/sql && npm install && node exporter_role.test.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const read = (f) => readFileSync(new URL(f, dir), 'utf8');
const guard  = read('0025_identity_checks_all_users.up.sql');
const signup = read('0026_self_signup_insert_policies.up.sql');
const roles  = read('0028_one_role_per_person.up.sql');
const up     = read('0029_exporter_role_international_phones.up.sql');
const down   = read('0029_exporter_role_international_phones.down.sql');

const db = new PGlite();

// The same live-census fixture as one_role_per_person.test.mjs: base-schema
// columns, RLS on all four tables, the nine policies, the placeholder-exempt
// unique index, and the anon=arwdDxtm grants the census reported.
await db.exec(`
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
  create schema auth;
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema public, auth to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;

  create table public.farmers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null, phone text not null, email text unique,
    district text, sub_county text, village text,
    farm_size_acres numeric, crops_grown text[],
    is_verified boolean default false, is_premium boolean default false,
    profile_photo text, otp_code text, otp_expires_at timestamptz,
    preferred_language text,
    created_at timestamptz default now(), updated_at timestamptz default now()
  );
  create table public.buyers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null, phone text not null unique, email text unique,
    buyer_type text default 'individual'
      check (buyer_type in ('individual','vendor','hotel','restaurant','exporter','ngo')),
    district text, business_name text, is_verified boolean default false,
    otp_code text, otp_expires_at timestamptz, created_at timestamptz default now()
  );
  create table public.transporters (
    id uuid primary key default gen_random_uuid(),
    supabase_uid uuid unique, full_name text, phone text, email text,
    status text default 'pending', capacity_kg numeric, district text,
    created_at timestamptz default now()
  );
  create table public.user_roles (
    user_id uuid primary key,
    role text not null default 'user' check (role in ('user','admin','superadmin')),
    granted_by uuid, granted_at timestamptz not null default now()
  );

  alter table public.farmers enable row level security;
  alter table public.buyers enable row level security;
  alter table public.transporters enable row level security;
  alter table public.user_roles enable row level security;

  create policy farmers_owner_update on public.farmers
    for update to authenticated using (auth.uid() = id) with check (auth.uid() = id);
  create policy farmers_public_select on public.farmers for select to public using (true);
  create policy buyers_owner_select on public.buyers
    for select to authenticated using (auth.uid() = id);
  create policy "transporter read own" on public.transporters
    for select to authenticated using (supabase_uid = auth.uid());
  create policy "transporter update own availability" on public.transporters
    for update to authenticated using (supabase_uid = auth.uid())
    with check (supabase_uid = auth.uid());
  create policy transporters_service_role on public.transporters
    for all to service_role using (true) with check (true);
  create policy user_roles_no_public on public.user_roles
    for all using (false) with check (false);
  create policy user_roles_service on public.user_roles
    for all to service_role using (true) with check (true);

  create unique index farmers_phone_unique on public.farmers (phone)
    where phone <> '+10000000000';

  grant all on public.farmers, public.buyers, public.transporters, public.user_roles
    to anon, authenticated, service_role;
`);

let passed = 0;
let r;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql); } finally { await db.exec('reset role;'); }
};
const rejects = async (fn, re, name) => { await assert.rejects(fn, re); ok(name); };
const rejectsCode = async (fn, code, re, name) => {
  const e = await fn().then(() => null, (err) => err);
  assert.ok(e, `expected ${code}, but the statement succeeded`);
  assert.equal(e.code, code, `wrong SQLSTATE (got ${e.code}): ${e.message}`);
  assert.match(e.message, re);
  ok(name);
};

const EXP  = '77777777-7777-7777-7777-777777777777';  // the Egyptian exporter
const KEN  = '88888888-8888-8888-8888-888888888888';  // a Kenyan buyer
const RPC1 = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';  // saves through the RPC
const DRV  = 'cccccccc-cccc-cccc-cccc-cccccccccccc';  // a local driver
const EGYPT = '+201036797717';                        // Five Trading & Supplies
const RPC_EGYPT = '+201036797719';                    // a second Egyptian number:
                                                     // the RPC's own uniqueness check
                                                     // would refuse EGYPT twice over
const UG    = '+256771234567';

// ── 1. The chain up to 0028, and the hole still open ────────────────────────
await db.exec(guard);
await db.exec(signup);
await db.exec(roles);

await rejectsCode(() => as('authenticated', EXP,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${EXP}', 'Ibrahim Hamroush', '${EGYPT}', 'Cairo')`),
  '23514', /phone_uganda|check constraint/,
  'before 0029 an Egyptian number cannot be stored — the company cannot register');

await rejects(() => as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role)
     values ('${EXP}', 'user', 'exporter')`),
  /user_roles_business_role_chk|check constraint/,
  '…and 0028 has no word for what they are');

// ── 2. Apply 0029 ───────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0029 is safe to re-run');

r = await db.query(`select constraint_name from information_schema.check_constraints
                     where constraint_schema = 'public'
                       and constraint_name in ('farmers_phone_e164','buyers_phone_e164',
                                               'transporters_phone_uganda')
                     order by 1`);
assert.deepEqual(r.rows.map(x => x.constraint_name),
  ['buyers_phone_e164', 'farmers_phone_e164', 'transporters_phone_uganda']);
ok('farmers and buyers carry the E.164 rule; transporters keep +256');

r = await db.query(`select pg_get_constraintdef(oid) as d from pg_constraint
                     where conname = 'user_roles_business_role_chk'`);
assert.match(r.rows[0].d, /exporter/);
ok('the role store now admits exporter');

// ── 3. A foreign seller registers, and stays ONE role ───────────────────────
r = await as('authenticated', EXP,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${EXP}', 'Ibrahim Hamroush', '${EGYPT}', 'Cairo')
     on conflict (id) do update
       set full_name = excluded.full_name, phone = excluded.phone,
           district = excluded.district`);
assert.equal(r.rows.length, 0);
ok('the exporter creates their own profile row with their own number, as themselves');

r = await as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role)
     values ('${EXP}', 'user', 'exporter') returning business_role`);
assert.deepEqual(r.rows[0], { business_role: 'exporter' });
ok('the backend can record exporter — the word the API writes before the row exists');

await rejectsCode(() => as('service_role', null,
  `insert into public.buyers (id, full_name, phone)
     values ('${EXP}', 'Ibrahim Hamroush', '+201036797718')`),
  'GP001', /already registered as a farmer/,
  'an exporter is still one role: 0028’s trigger refuses them a buyers row too');

r = await as('authenticated', EXP,
  `update public.farmers set district = 'Alexandria' where id = '${EXP}'`);
assert.equal(r.rows.length, 0);
ok('editing the profile keeps working — the foreign number passes the new CHECK on UPDATE');

// ── 4. The shape rule, measured at the edges ────────────────────────────────
r = await as('authenticated', KEN,
  `insert into public.buyers (id, full_name, phone, district)
     values ('${KEN}', 'Grace Nabirye', '+254700123456', 'Nairobi')`);
assert.equal(r.rows.length, 0);
ok('a Kenyan buyer stores too — E.164 is not a second Ugandan-only rule');

for (const bad of ['0771234567', '+256', '+0123456789', 'not-a-phone']) {
  await rejectsCode(() => as('service_role', null,
    `insert into public.farmers (id, full_name, phone)
       values (gen_random_uuid(), 'Valid Name', '${bad}')`),
    '23514', /phone_e164|check constraint/,
    `the stored shape is refused for '${bad}' — normalisation happens before the write`);
}

// The placeholder satisfies E.164 (the database cannot tell it from a real North
// American number); refusing it is the API's job, pinned in test_profile_guard.py.
r = await as('service_role', null,
  `insert into public.farmers (id, full_name, phone)
     values (gen_random_uuid(), 'Legacy Placeholder', '+10000000000')`);
assert.equal(r.rows.length, 0);
ok('the placeholder still satisfies the shape rule — its refusal lives in normalize_phone');

await rejectsCode(() => as('service_role', null,
  `insert into public.transporters (supabase_uid, full_name, phone, status)
     values ('${DRV}', 'Peter Okello', '${EGYPT}', 'active')`),
  '23514', /phone_uganda|check constraint/,
  'a driver with a foreign number is refused — transporters were deliberately not widened');

r = await as('service_role', null,
  `insert into public.transporters (supabase_uid, full_name, phone, status)
     values ('${DRV}', 'Peter Okello', '${UG}', 'active')`);
assert.equal(r.rows.length, 0);
ok('a local driver is unchanged');

for (const bad of ['buyer', 'admin', 'superadmin', 'input_seller']) {
  await rejects(() => as('service_role', null,
    `insert into public.user_roles (user_id, role, business_role)
       values ('${KEN}', 'user', '${bad}')`),
    /user_roles_business_role_chk|check constraint/,
    `business_role still refuses '${bad}'`);
}

// ── 5. The RPC agrees with the table ────────────────────────────────────────
r = await as('authenticated', RPC1,
  `select public.update_my_profile('Ibrahim Hamroush', '${RPC_EGYPT}', 'Cairo') as j`);
assert.deepEqual(r.rows[0].j, { ok: true });
r = await db.query(`select phone from public.farmers where id = '${RPC1}'`);
assert.equal(r.rows[0].phone, RPC_EGYPT);
ok('update_my_profile stores an Egyptian number (it no longer contradicts the table)');

await rejectsCode(() => as('authenticated', RPC1,
  `select public.update_my_profile(null, '0771234567', null)`),
  '22023', /country code/,
  'the RPC refuses a number without a country code');

await rejectsCode(() => as('authenticated', RPC1,
  `select public.update_my_profile('unknown user', null, null)`),
  '22023', /real full name/,
  'the RPC carries 0025’s full blacklist, not 0008’s shorter one');

await rejectsCode(() => as('authenticated', KEN,
  `select public.update_my_profile('Grace Nabirye', '${RPC_EGYPT}', null)`),
  '23505', /already registered/,
  'one phone is still one account, foreign numbers included');

// ── 6. The rollback restores the old rules without touching rows ────────────
await db.exec(down);

r = await db.query(`select count(*)::int as n from public.farmers
                     where phone in ('${EGYPT}','${RPC_EGYPT}')`);
assert.equal(r.rows[0].n, 2);
ok('rollback reads and changes no rows — the exporter profiles survive it');

await rejects(() => as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role)
     values ('${KEN}', 'user', 'exporter')`),
  /user_roles_business_role_chk|check constraint/,
  'after rollback exporter is not a storable word again');

await rejectsCode(() => as('authenticated', EXP,
  `update public.farmers set district = 'Giza' where id = '${EXP}'`),
  '23514', /phone_uganda|check constraint/,
  'a foreign number written under 0029 fails its next UPDATE after rollback — the '
  + 'NOT VALID pressure 0025 named, in reverse (documented in the down file)');

await db.exec(up);
r = await as('authenticated', EXP,
  `update public.farmers set district = 'Giza' where id = '${EXP}'`);
assert.equal(r.rows.length, 0);
ok('re-applying 0029 makes those profiles editable again');

console.log(`\n${passed} passed`);
await db.close();