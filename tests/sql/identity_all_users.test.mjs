/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0025 against a real PostgreSQL engine (PGlite).
//
// This file does NOT reproduce supabase/base-schema.sql. It reproduces what the
// live project was MEASURED to contain on 2026-11-04, because the two differ and
// the difference is the defect:
//
//   farmers_phone_format   CHECK (phone ~ '^[+][1-9][0-9]{6,14}$')   <- generic, not 0008's
//   farmers_full_name_nonempty                                        <- absent
//   farmers_phone_unique   valid, WHERE (phone <> '+10000000000')     <- exempts the fake number
//   buyers / transporters   no identity rule of any kind
//   farmers, buyers, user_roles   anon = arwdDxtm                     <- 0014 never reached them
//
// Everything the census returned is asserted here before the migration runs, so
// if this file passes, 0025 was tested against the production shape rather than
// against the file that was supposed to have produced it.
//
// Run:  cd tests/sql && npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const up = readFileSync(new URL('0025_identity_checks_all_users.up.sql', dir), 'utf8');
const down = readFileSync(new URL('0025_identity_checks_all_users.down.sql', dir), 'utf8');

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

  -- farmers: the live column set, with the live (weak) phone rule and the live
  -- (exempting) unique index. The placeholder rows are inserted first so the
  -- index has to be created over them, exactly as it was in production.
  create table public.farmers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null,
    phone text,
    email text unique,
    district text,
    sub_county text, village text,
    farm_size_acres numeric,
    crops_grown text[],
    is_verified boolean default false,
    is_premium boolean default false,
    profile_photo text,
    preferred_language text,
    otp_code text, otp_expires_at timestamptz,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
  );
  alter table public.farmers add constraint farmers_phone_format
    check (phone ~ '^[+][1-9][0-9]{6,14}$');
  insert into public.farmers (full_name, phone, district) values
    ('Julius Jesus',      '+10000000000', null),
    ('Anzuman Begum',     '+10000000000', null),
    ('Aitasi Godfrey',    '+10000000000', null),
    ('NANDAWULA VIOLET',  '+10000000000', null),
    ('ISSA Wislar',       '+10000000000', null),
    ('Mugumyazeal Zeal',  '+10000000000', null),
    ('Josephine nabatanzi','+10000000000', null),
    ('Unknown User',      '+10000000000', null),
    ('Sarah Auma',        '+256771234567', 'Kasese');
  create unique index farmers_phone_unique on public.farmers (phone)
    where phone <> '+10000000000';

  create table public.buyers (
    id uuid primary key default gen_random_uuid(),
    full_name text, phone text, email text unique,
    buyer_type text default 'individual'
      check (buyer_type in ('individual','vendor','hotel','restaurant','exporter','ngo')),
    district text, business_name text,
    is_verified boolean default false,
    otp_code text, otp_expires_at timestamptz,
    created_at timestamptz default now()
  );
  insert into public.buyers (full_name, phone, email) values
    ('Grace Nabirye', '+256772444555', 'grace@example.com');

  create table public.transporters (
    id uuid primary key default gen_random_uuid(),
    full_name text, phone text, email text,
    vehicle_type text, capacity_kg numeric, plate_number text,
    status text default 'inactive' check (status in ('active','inactive')),
    is_available boolean default true,
    district text, notes text, supabase_uid uuid,
    created_at timestamptz default now()
  );
  insert into public.transporters (full_name, phone, status) values
    ('Bob Katumba', '+256700999888', 'active');

  create table public.user_roles (
    user_id uuid primary key,
    role text not null default 'user',
    granted_by uuid, granted_at timestamptz not null default now(),
    constraint user_roles_role_chk check (role in ('user','admin','superadmin'))
  );

  alter table public.farmers enable row level security;
  alter table public.buyers enable row level security;
  alter table public.user_roles enable row level security;
  create policy farmers_owner_update on public.farmers
    for update to authenticated using (auth.uid() = id) with check (auth.uid() = id);
  create policy farmers_public_select on public.farmers for select to public using (true);
  create policy buyers_owner_select on public.buyers
    for select to authenticated using (auth.uid() = id);

  -- Supabase's default table privileges: this is where anon=arwdDxtm comes from.
  grant all on public.farmers, public.buyers, public.transporters, public.user_roles
    to anon, authenticated, service_role;
`);

let passed = 0;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql); } finally { await db.exec('reset role;'); }
};
const rejects = async (fn, re, name) => { await assert.rejects(fn, re); ok(name); };
const priv = async (role, table, kind) =>
  (await db.query(`select has_table_privilege('${role}', 'public.${table}', '${kind}') as p`)).rows[0].p;

const MINE = '22222222-2222-2222-2222-222222222222';
await db.exec(`update public.farmers set id = '${MINE}' where full_name = 'Julius Jesus'`);
let r;

// ── 1. Reproduce the measured production state ──────────────────────────────
r = await db.query(`select count(*) as n from public.farmers where phone = '+10000000000'`);
assert.equal(r.rows[0].n, 8);
ok('8 accounts share one phone number, as the census reported');

r = await db.query(`select indisvalid, pg_get_expr(x.indpred, x.indrelid) as pred
                      from pg_index x join pg_class c on c.oid = x.indexrelid
                     where c.relname = 'farmers_phone_unique'`);
assert.equal(r.rows[0].indisvalid, true);
assert.match(r.rows[0].pred, /phone <>\s*'\+10000000000'/);
ok('the unique index is HEALTHY and carries the exemption in its predicate');

await as('service_role', null, `insert into public.farmers (full_name, phone)
                                  values ('Ninth Person','+10000000000')`);
r = await db.query(`select count(*) as n from public.farmers where phone = '+10000000000'`);
assert.equal(r.rows[0].n, 9);
ok('…so a ninth placeholder account is accepted without complaint');

await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Clone','+256771234567')`),
  /farmers_phone_unique/, 'while a duplicate REAL number is still refused — the index works, selectively');

await as('service_role', null, `insert into public.farmers (full_name, phone)
                                  values ('', '+256733111222')`);
ok('an empty name is accepted — farmers_full_name_nonempty does not exist live');
await as('service_role', null, `insert into public.buyers (full_name, phone)
                                  values ('x', '+15551234567')`);
ok('a one-character name and a US number are accepted on buyers — no rule there at all');

for (const t of ['farmers', 'buyers', 'user_roles'])
  assert.equal(await priv('anon', t, 'update'), true);
assert.equal(await priv('anon', 'user_roles', 'insert'), true);
ok('anon holds INSERT and UPDATE on farmers, buyers and user_roles (0014 never reached them)');

// ── 2. Apply ────────────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0025 is safe to re-run');

r = await db.query(`select conname, convalidated from pg_constraint
                     where conname in ('farmers_full_name_real','farmers_phone_uganda',
                                       'buyers_full_name_real','transporters_phone_uganda')
                     order by conname`);
assert.equal(r.rows.length, 4);
assert.equal(r.rows.filter(x => !x.convalidated).length, 4);
ok('all four new rules installed NOT VALID — no existing row was re-checked');

r = await db.query(`select count(*) as n from public.farmers where phone = '+10000000000'`);
assert.equal(r.rows[0].n, 9);
ok('and the 9 placeholder rows are still there: 0025 rewrote no data');

// ── 3. New writes must satisfy the rules ────────────────────────────────────
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Tenth','+10000000000')`),
  /farmers_phone_uganda/, 'a new +10000000000 account is now refused by the database');
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Tenth','+256111222333')`),
  /farmers_phone_uganda/, 'a +256 number that is not a mobile prefix is refused');
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Tenth','0771234567')`),
  /farmers_phone_uganda/, '…and so is an un-normalised local number');
await as('service_role', null, `insert into public.farmers (full_name, phone)
                                  values ('Real Person','+256776543210')`);
ok('a genuine Ugandan mobile still registers');

await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('  ','+256776543211')`),
  /farmers_full_name_real/, 'a whitespace name is refused');
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Unknown User','+256776543212')`),
  /farmers_full_name_real/, '…and so is the exact placeholder name found in production');
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values (null,'+256776543213')`),
  /not-null constraint/, 'a NULL name was already impossible (base-schema.sql:8 NOT NULL) — '
                       + 'the new rule is what catches the fake ones');

await rejects(() => as('service_role', null, `insert into public.buyers (full_name, phone)
                                                 values ('Grace N','kenya number')`),
  /buyers_phone_uganda/, 'buyers now have a phone rule too (they had none)');
await rejects(() => as('service_role', null, `insert into public.transporters (full_name, phone)
                                                 values ('','+256700111999')`),
  /transporters_full_name_real/, 'and transporters have a name rule (they had none)');

// The documented consequence, asserted rather than promised in a comment: a
// legacy row cannot be edited by anyone until its identity is fixed.
await rejects(() => as('service_role', null,
  `update public.farmers set is_verified = true where phone = '+10000000000'`),
  /farmers_phone_uganda/,
  'KNOWN CONSEQUENCE: an admin cannot verify a placeholder account until its phone is fixed');

// ── 4. Privileges ───────────────────────────────────────────────────────────
// Measured, and deliberately NOT fixed here. Every write helper in app.py sends
// the publishable key as its bearer token (app.py:395 supa_insert, :421
// supa_update, :439 supa_delete, :5583 the profile upsert), so the production
// write path runs as anon. Taking anon's writes away in this migration would not
// remove a capability anyone uses — it would stop orders, listings, contact
// messages and profile saves from being writable at all. The revoke belongs to
// the stage that first moves those helpers onto a real service identity, and
// tests/test_write_path_identity.py fails if that stage happens without this
// block being rewritten with it.
for (const t of ['farmers', 'buyers', 'user_roles']) {
  assert.equal(await priv('anon', t, 'update'), true, `anon ${t} update`);
  assert.equal(await priv('anon', t, 'insert'), true, `anon ${t} insert`);
}
ok('MEASURED, NOT FIXED: anon can still write farmers, buyers and user_roles — '
   + 'RLS is the only thing deciding whether an anon request lands');

assert.equal(await priv('authenticated', 'user_roles', 'insert'), false);
assert.equal(await priv('authenticated', 'user_roles', 'update'), false);
ok('a signed-in user cannot grant themselves admin at the privilege layer');

await rejects(() => as('authenticated', MINE,
  `update public.farmers set is_verified = true where id = '${MINE}'`),
  /permission denied/, 'a farmer still cannot declare themselves verified (0024 scope, repeated)');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set otp_code = '000000' where id = '${MINE}'`),
  /permission denied/, '…nor write their own one-time code');

r = await as('service_role', null, `update public.farmers set phone = '+256778889990'
                                      where id = '${MINE}' returning phone`);
assert.equal(r.rows[0].phone, '+256778889990');
r = await as('authenticated', MINE,
  `update public.farmers set full_name = 'Julius Jesus Mukasa', district = 'Mukono',
          profile_photo = 'https://example.com/j.jpg', preferred_language = 'lg'
     where id = '${MINE}'
   returning full_name, district, profile_photo, preferred_language`);
assert.equal(r.rows[0].full_name, 'Julius Jesus Mukasa');
assert.equal(r.rows[0].preferred_language, 'lg');
ok('once the phone is real, the profile screen writes every column it needs');

await rejects(() => as('authenticated', MINE,
  `update public.buyers set is_verified = true where id = '${MINE}'`),
  /permission denied/, 'buyers: authenticated writes nothing (no client code writes this table)');
await rejects(() => as('authenticated', MINE,
  `update public.transporters set status = 'active' where id = '${MINE}'`),
  /permission denied/, 'transporters: a driver cannot undo their own deactivation (0022 scope, applied here)');

// ── 5. Reads and the backend are untouched ──────────────────────────────────
r = await as('anon', null, `select full_name from public.farmers`);
assert.ok(r.rows.length >= 2);
ok('anon still reads farmer profiles, so the public browse pages are unchanged');
assert.equal(await priv('authenticated', 'transporters', 'select'), true);
ok('authenticated keeps SELECT on transporters (0014:55 granted it; 0025 keeps it)');

r = await as('service_role', null,
  `update public.user_roles set role = 'superadmin' where user_id = '${MINE}'`);
r = await as('service_role', null, `insert into public.user_roles (user_id, role)
                                     values ('${MINE}', 'admin') returning role`);
assert.equal(r.rows[0].role, 'admin');
ok('the admin console still manages roles through service_role, which bypasses all of this');

// ── 6. Down ─────────────────────────────────────────────────────────────────
await db.exec(down);
assert.equal(await priv('anon', 'farmers', 'update'), true);
assert.equal(await priv('anon', 'user_roles', 'insert'), true);
ok('anon is exactly where it was: 0025 never touched it, so neither does the rollback');

assert.equal(await priv('authenticated', 'farmers', 'update'), true);
r = await as('authenticated', MINE,
  `update public.farmers set is_verified = false where id = '${MINE}' returning id`);
assert.equal(r.rows.length, 1);
ok('the rollback re-opens the self-verification hole, as a rollback must');
assert.equal(await priv('authenticated', 'user_roles', 'insert'), false);
ok('…but leaves buyers/transporters/user_roles writes closed, because a rollback '
   + 'that could not tell 0014 from 0025 must not guess them open');

r = await db.query(`select pg_get_constraintdef(oid) as d from pg_constraint
                     where conname = 'farmers_phone_format'`);
assert.match(r.rows[0].d, /\[\+\]\[1-9\]\[0-9\]\{6,14\}/);
ok('…and puts the generic international rule back in place of the +256 one');
await as('service_role', null, `insert into public.farmers (full_name, phone)
                                  values ('Again','+10000000000')`);
ok('…and a placeholder account can be created again');

await db.exec(up);
await rejects(() => as('service_role', null, `insert into public.farmers (full_name, phone)
                                                 values ('Once more','+10000000000')`),
  /farmers_phone_uganda/, 'and 0025 can be applied again after that rollback');

console.log(`\n${passed} all-users identity assertions passed`);
