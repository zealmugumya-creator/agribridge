/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0026 against a real PostgreSQL engine (PGlite).
//
// The 2026-11-05 RLS census returned nine policies across four tables and not one
// of them is an INSERT. So a signed-in browser cannot create its own profile row:
// Postgres refuses the write before the phone, the name rule or the uniqueness
// index is ever consulted. That is why signup leaves a person with an auth account
// and no profile, why the app says "Profile could not be saved", and why the
// answer people find is to register again with a different email — which is the
// duplicate-account complaint the owner raised, seen from the wrong side.
//
// This file builds the census state exactly (policies, rls_enabled, the
// anon=arwdDxtm grants the same query reported), reproduces the refusal, adds the
// missing policies, and proves they are row-scoped: your own row yes, anyone
// else's no, and no self-promotion through user_roles.
//
// Run:  cd tests/sql && npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const guard = readFileSync(new URL('0025_identity_checks_all_users.up.sql', dir), 'utf8');
const up = readFileSync(new URL('0026_self_signup_insert_policies.up.sql', dir), 'utf8');
const down = readFileSync(new URL('0026_self_signup_insert_policies.down.sql', dir), 'utf8');

const db = new PGlite();

// The live project as the census measured it. Every policy below is one of the
// nine rows that query returned, with the same command and the same roles.
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
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null, role text not null,
    granted_by uuid, granted_at timestamptz not null default now()
  );

  -- rls_enabled = true on all four, exactly as measured.
  alter table public.farmers enable row level security;
  alter table public.buyers enable row level security;
  alter table public.transporters enable row level security;
  alter table public.user_roles enable row level security;

  -- The census' relacl: anon holds every write privilege on these tables.
  grant all on public.farmers, public.buyers, public.transporters, public.user_roles
    to anon, authenticated, service_role;

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

  -- 0008's rules were never applied either, so the live index carries the
  -- exemption predicate the census read out of pg_index.
  create unique index farmers_phone_unique on public.farmers (phone)
    where phone <> '+10000000000';
`);

let passed = 0;
let r;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql); } finally { await db.exec('reset role;'); }
};
const rejects = async (fn, re, name) => { await assert.rejects(fn, re); ok(name); };

const MINE = '22222222-2222-2222-2222-222222222222';
const OTHER = '33333333-3333-3333-3333-333333333333';

await db.exec(`
  insert into public.farmers (id, full_name, phone, district) values
    ('${OTHER}', 'Sarah Auma', '+256771234567', 'Kasese');
  insert into public.transporters (supabase_uid, full_name, status)
    values ('${OTHER}', 'Allan Magembe', 'active');
`);

// ── 1. Reproduce the production failure ─────────────────────────────────────
// This is the exact statement saveProfileWithRetry (static/index.html:3409)
// causes the backend to run for a brand-new account, seconds after GoTrue has
// created the auth user.
await rejects(() => as('authenticated', MINE,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${MINE}', 'Julius Jesus Mukasa', '+256778889990', 'Mukono')`),
  /row-level security policy/,
  'a new farmer cannot create their own profile row — the census had no INSERT policy');

await rejects(() => as('authenticated', MINE,
  `insert into public.buyers (id, full_name, phone)
     values ('${MINE}', 'Grace Nabirye', '+256778889991')`),
  /row-level security policy/, '…nor can a new buyer');

await rejects(() => as('authenticated', MINE,
  `insert into public.transporters (supabase_uid, full_name)
     values ('${MINE}', 'Peter Okello')`),
  /row-level security policy/, '…nor a new transporter');

// The other half of the owner's complaint, measured rather than assumed: the
// unauthenticated role holds every write privilege, and RLS is what stops it.
await rejects(() => as('anon', null,
  `insert into public.farmers (full_name, phone, district)
     values ('Ghost Account', '+256778889992', 'Kampala')`),
  /row-level security policy/,
  'anon holds the privilege but RLS refuses the write — today, the policy is the only door');

r = await as('service_role', null,
  `insert into public.farmers (full_name, phone, district)
     values ('Admin Created', '+256778889993', 'Wakiso') returning id`);
assert.equal(r.rows.length, 1);
ok('service_role writes freely, so the admin console is not what this migration fixes');

await as('service_role', null, `delete from public.farmers where full_name in
  ('Admin Created')`);

// ── 2. Apply ────────────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0026 is safe to re-run');

r = await db.query(`select c.relname as t, p.polname, p.polcmd::text as cmd
                     from pg_policy p join pg_class c on c.oid = p.polrelid
                    where p.polname in ('farmers_owner_insert','buyers_owner_insert',
                                         'transporters_owner_insert')
                    order by 1`);
// polcmd is a single char and INSERT is 'a', not 'i' — pg_policy uses the same
// letters as the old ascmd grammar: r select, a insert, d delete, u update.
assert.deepEqual(r.rows.map(x => `${x.t}:${x.cmd}`),
  ['buyers:a', 'farmers:a', 'transporters:a']);
ok('exactly three INSERT policies now exist, one per role table');

// ── 3. A person can create their own row ────────────────────────────────────
r = await as('authenticated', MINE,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${MINE}', 'Julius Jesus Mukasa', '+256778889990', 'Mukono')
   returning id, full_name, district`);
assert.equal(r.rows.length, 1);
assert.equal(r.rows[0].district, 'Mukono');
ok('signup now works: the new farmer created their own profile row');

r = await as('authenticated', MINE,
  `insert into public.buyers (id, full_name, phone)
     values ('${MINE}', 'Grace Nabirye', '+256778889991') returning id`);
assert.equal(r.rows.length, 1);
ok('…and so does a buyer');

r = await as('authenticated', MINE,
  `insert into public.transporters (supabase_uid, full_name)
     values ('${MINE}', 'Peter Okello') returning supabase_uid`);
assert.equal(r.rows.length, 1);
ok('…and a transporter, keyed on supabase_uid because that is what 0014 keys on');

// ── 4. …and that is all a person can do ─────────────────────────────────────
await rejects(() => as('authenticated', MINE,
  `insert into public.farmers (id, full_name, phone)
     values ('${OTHER}', 'Impostor', '+256778889994')`),
  /row-level security policy/, 'you cannot create somebody else’s farmer profile');

await rejects(() => as('authenticated', MINE,
  `insert into public.transporters (supabase_uid, full_name)
     values ('${OTHER}', 'Impostor Driver')`),
  /row-level security policy/, '…nor claim another person’s transporter slot');

await rejects(() => as('authenticated', MINE,
  `insert into public.user_roles (user_id, role) values ('${MINE}', 'superadmin')`),
  /row-level security policy/,
  'and creating your own profile does not create a path to granting yourself admin');

await rejects(() => as('anon', null,
  `insert into public.farmers (full_name, phone) values ('Ghost', '+256778889995')`),
  /permission denied/,
  'anon is now refused by the privilege layer too, not only by the policy');

assert.equal(await (await db.query(
  `select has_table_privilege('anon','public.farmers','insert') as p`)).rows[0].p, false);
assert.equal(await (await db.query(
  `select has_table_privilege('anon','public.user_roles','insert') as p`)).rows[0].p, false);
ok('relacl no longer says anon may write these tables, so turning RLS off in the '
   + 'dashboard stops being an escalation path');

r = await as('anon', null, `select full_name from public.farmers`);
assert.ok(r.rows.length >= 2);
ok('anon still reads profiles — the public browse pages are unchanged');

// The live table is not empty: eight profiles share +10000000000, and the
// census read the exemption predicate straight out of pg_index. Reproducing
// that here means 0025's NOT VALID clauses are tested against the state they
// will actually meet, and it is why the placeholder rows are inserted *before*
// the guard is applied.
await as('service_role', null,
  `insert into public.farmers (full_name, phone) values ('Placeholder One','+10000000000')`);
await as('service_role', null,
  `insert into public.farmers (full_name, phone) values ('Placeholder Two','+10000000000')`);

// ── 5. The whole signup → profile-edit sequence, with 0025 in place ─────────
await db.exec(guard);   // the name/phone rules and the farmers column scope
r = await as('authenticated', MINE,
  `update public.farmers set district = 'Ibanda', profile_photo = 'https://example.com/j.jpg'
     where id = '${MINE}' returning district`);
assert.equal(r.rows[0].district, 'Ibanda');
ok('a person who signed up can then edit their own profile (0025 grants stand together)');

await rejects(() => as('authenticated', MINE,
  `update public.farmers set is_verified = true where id = '${MINE}'`),
  /permission denied/, '…but still cannot grade themselves verified');

await rejects(() => as('authenticated', MINE,
  `insert into public.farmers (id, full_name, phone)
     values (gen_random_uuid(), 'Second Person', '+256778889996')`),
  /row-level security policy/,
  'one auth account still creates exactly one farmer profile — no silent second identity');

// The placeholder exemption is still live, and this test says so rather than
// implying 0025 or 0026 removed it. Two things, measured separately: the checks
// close the *inflow*, and nothing at all cleans the *pool*.
await rejects(() => as('service_role', null,
  `insert into public.farmers (full_name, phone) values ('Placeholder Three','+10000000000')`),
  /farmers_phone_uganda/,
  'a new +10000000000 account is now refused even by the backend — the inflow is closed');

r = await db.query(`select count(*)::int as n from public.farmers
                     where phone = '+10000000000'`);
assert.equal(r.rows[0].n, 2);
r = await db.query(`select pg_get_expr(i.indpred, i.indrelid) as p
                      from pg_index i join pg_class c on c.oid = i.indexrelid
                     where c.relname = 'farmers_phone_unique'`);
assert.match(r.rows[0].p, /10000000000/);
ok('MEASURED, NOT FIXED: the two legacy rows still share +10000000000, because '
   + 'farmers_phone_unique exempts it — that predicate is 0027’s question, not this one');

await rejects(() => as('service_role', null,
  `update public.farmers set district = 'Wakiso'
     where phone = '+10000000000' and full_name = 'Placeholder One'`),
  /farmers_phone_uganda/,
  '…and a legacy placeholder row cannot be edited on any column until its phone is fixed');

// ── 6. Down, and back ───────────────────────────────────────────────────────
// A uid with no profile row yet — OTHER already owns one, seeded above, and an
// insert of that id would collide on the primary key instead of proving whether
// the policy exists.
const LATE = '44444444-4444-4444-4444-444444444444';
await db.exec(down);
await rejects(() => as('authenticated', LATE,
  `insert into public.farmers (id, full_name, phone)
     values ('${LATE}', 'Late Signup', '+256778889997')`),
  /row-level security policy/,
  'the rollback puts signup back to broken, which is what it is supposed to restore');
assert.equal(await (await db.query(
  `select has_table_privilege('anon','public.farmers','insert') as p`)).rows[0].p, false);
ok('…but leaves anon’s revoked privileges revoked: a rollback must not guess that '
   + '0026 installed a grant that 0014 removed');

await db.exec(up);
r = await as('authenticated', LATE,
  `insert into public.farmers (id, full_name, phone)
     values ('${LATE}', 'Late Signup', '+256778889997') returning id`);
assert.equal(r.rows.length, 1);
ok('and 0026 can be applied again after that rollback');

console.log(`\n${passed} self-signup policy assertions passed`);
