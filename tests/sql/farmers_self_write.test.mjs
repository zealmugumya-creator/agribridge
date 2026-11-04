/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0024 against a real PostgreSQL engine (PGlite).
//
// farmers_owner_update (supabase/base-schema.sql:257) is row-scoped, so it lets a
// signed-in farmer PATCH any writable column of their own profile — including the
// two badges the buyer market sorts listings by and the one-time-code column whose
// READ base-schema.sql:285 bothers to revoke. This file builds that production
// state (base-schema columns + policies, Supabase's default grants, and migration
// 0008's name/phone validation), reproduces the hole, closes it, and proves the
// profile edits the web app really does make still work.
//
// Run:  cd tests/sql && npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const enforce = readFileSync(new URL('0008_enforce_profile_name_phone.up.sql', dir), 'utf8');
const up = readFileSync(new URL('0024_farmers_self_write_guard.up.sql', dir), 'utf8');
const down = readFileSync(new URL('0024_farmers_self_write_guard.down.sql', dir), 'utf8');

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

  -- Columns copied from supabase/base-schema.sql:6-21, deliberately including no
  -- preferred_language: the SPA PATCHes that column (static/index.html:12451) but
  -- no schema file in this repository declares it, so 0024 has to survive its
  -- absence rather than abort on it.
  create table public.farmers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null,
    phone text not null,
    email text unique,
    district text not null,
    sub_county text, village text,
    farm_size_acres numeric,
    crops_grown text[],
    is_verified boolean default false,
    is_premium boolean default false,
    profile_photo text,
    otp_code text, otp_expires_at timestamptz,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
  );

  alter table public.farmers enable row level security;
  -- Supabase grants ALL on public tables to these roles by default.
  grant all on public.farmers to anon, authenticated, service_role;

  -- Both policies verbatim from supabase/base-schema.sql:257-258.
  create policy farmers_owner_update on public.farmers
    for update to authenticated using (auth.uid() = id) with check (auth.uid() = id);
  create policy farmers_public_select on public.farmers for select to public using (true);

  -- And the one protection base-schema.sql:285 does declare.
  revoke select (otp_code, otp_expires_at) on public.farmers from anon, authenticated;
`);

let passed = 0;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql); } finally { await db.exec('reset role;'); }
};
const rejects = async (fn, re, name) => { await assert.rejects(fn, re); ok(name); };

const MINE = '22222222-2222-2222-2222-222222222222';
const OTHER = '33333333-3333-3333-3333-333333333333';

await db.exec(`
  insert into public.farmers (id, full_name, phone, district, email) values
    ('${MINE}', 'Sarah Auma', '+256771234567', 'Kasese', 'sarah@example.com'),
    ('${OTHER}', 'Allan Magembe', '+256700111222', 'Jinja', 'allan@example.com');
`);

// ── Reproduce production exactly as the schema files declare it ─────────────
let r = await as('authenticated', MINE,
  `update public.farmers set is_verified = true, is_premium = true
     where id = '${MINE}' returning is_verified, is_premium`);
assert.equal(r.rows[0].is_verified, true);
ok('before 0024 a farmer can declare themselves verified and premium');

r = await as('authenticated', MINE,
  `update public.farmers set otp_code = '000000' where id = '${MINE}' returning otp_code`);
assert.equal(r.rows[0].otp_code, '000000');
ok('…and can write the one-time code whose READ the base schema bothers to revoke');

r = await as('authenticated', MINE,
  `update public.farmers set email = 'someone-else@example.com'
     where id = '${MINE}'`);
r = await as('service_role', null, `select email from public.farmers where id = '${MINE}'`);
assert.equal(r.rows[0].email, 'someone-else@example.com');
ok('…and can desynchronise the row from the account behind it');

// Measured here, and deliberately NOT fixed by 0024. base-schema.sql:285 revokes
// READ of otp_code/otp_expires_at from anon and authenticated, but a whole-table
// SELECT grant cannot be reduced by a column revoke — Postgres applies column
// grants only when no table-wide grant stands. Supabase's default privileges put
// a table-wide grant on every new public table, so the revoke is a no-op and the
// code a future OTP step stores is readable by any signed-in browser today.
// Closing the READ means revoking table-wide SELECT and then listing every column
// the app reads, `select *` calls and farmers(*) embeds included — a stage of its
// own, not a line to slip into this one.
r = await as('authenticated', MINE, `select otp_code from public.farmers where id = '${MINE}'`);
assert.equal(r.rows.length, 1);
ok('MEASURED, NOT FIXED: the one-time code is still READABLE (base-schema revoke is a no-op)');

await as('service_role', null,
  `update public.farmers set email = 'sarah@example.com', is_verified = false,
          is_premium = false, otp_code = null where id = '${MINE}'`);

// ── Apply the fix ───────────────────────────────────────────────────────────
await db.exec(enforce);        // 0008: the identity FIELDS are already guarded
await db.exec(up);
await db.exec(up);
ok('0024 is safe to re-run');

await rejects(() => as('authenticated', MINE,
  `update public.farmers set is_verified = true where id = '${MINE}'`),
  /permission denied for table farmers/, 'a farmer can no longer declare themselves verified');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set is_premium = true where id = '${MINE}'`),
  /permission denied/, '…nor premium');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set otp_code = '000000' where id = '${MINE}'`),
  /permission denied/, '…nor write their own one-time code');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set otp_expires_at = now() + interval '10 minutes' where id = '${MINE}'`),
  /permission denied/, '…nor its expiry, which is what makes a planted code usable');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set email = 'someone-else@example.com' where id = '${MINE}'`),
  /permission denied/, '…nor re-point the email off the verified account');

// ── The profile edits the web app actually makes must survive ───────────────
r = await as('authenticated', MINE,
  `update public.farmers set full_name = 'Sarah Auma Okello', phone = '+256771999888',
          district = 'Ibanda', profile_photo = 'https://example.com/s.jpg'
     where id = '${MINE}' returning full_name, phone, district, profile_photo`);
assert.equal(r.rows.length, 1);
assert.equal(r.rows[0].full_name, 'Sarah Auma Okello');
ok('name, phone, district and photograph still save (index.html:7850, :8495, :8507)');

r = await as('service_role', null,
  `select sub_county, village, farm_size_acres, crops_grown from public.farmers where id = '${MINE}'`);
assert.equal(r.rows[0].sub_county, null);
ok('0024 leaves the rest of the row alone: it rewrites no data');

// ── 0008's own guards still hold next to the new column scope ──────────────
await rejects(() => as('authenticated', MINE,
  `update public.farmers set full_name = 'S' where id = '${MINE}'`),
  /farmers_full_name_nonempty/, 'a one-character name is still refused');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set full_name = '   ' where id = '${MINE}'`),
  /farmers_full_name_nonempty/, '…and so is a name made of spaces');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set phone = '0771 234 567' where id = '${MINE}'`),
  /farmers_phone_format/, 'a number that is not +256 format is refused');
await rejects(() => as('authenticated', MINE,
  `update public.farmers set phone = '+256700111222' where id = '${MINE}'`),
  /farmers_phone_unique/, 'one phone still means one account');
await rejects(() => as('service_role', null,
  `insert into public.farmers (full_name, phone, district)
     values ('Copycat', '+256771999888', 'Kampala')`),
  /farmers_phone_unique/, '…including for the backend that bypasses RLS');

// ── Everyone else keeps exactly the access they had ─────────────────────────
r = await as('authenticated', OTHER,
  `update public.farmers set full_name = 'Hijack' where id = '${MINE}' returning id`);
assert.equal(r.rows.length, 0);
ok('another farmer still cannot write this row at all');

r = await as('authenticated', MINE, `select full_name, is_verified from public.farmers where id = '${MINE}'`);
assert.equal(r.rows[0].full_name, 'Sarah Auma Okello');
ok('the profile is still readable, so buyers keep seeing farmer pages');

// The otp_code READ is still open after 0024, exactly as it was before it — this
// migration narrows writes, and the read leak is reported rather than papered over.
r = await as('authenticated', MINE, `select otp_expires_at from public.farmers`);
assert.equal(r.rows.length, 2);
ok('and yes, that read leak survives 0024 too — stated, not hidden');

await rejects(() => as('anon', null,
  `update public.farmers set district = 'Nowhere' where id = '${MINE}'`),
  /permission denied/, 'anon writes nothing');
r = await as('anon', null, `select full_name from public.farmers`);
assert.equal(r.rows.length, 2);
ok('anon still reads the public profile list');

r = await as('service_role', null,
  `update public.farmers set is_verified = true, is_premium = true, otp_code = '123456'
     where id = '${MINE}' returning is_verified, is_premium, otp_code`);
assert.equal(r.rows[0].is_verified, true);
assert.equal(r.rows[0].otp_code, '123456');
ok('the admin API and any future OTP step keep writing every column via service_role');

// ── Down restores the hole, honestly ────────────────────────────────────────
await db.exec(down);
r = await as('authenticated', MINE,
  `update public.farmers set is_verified = false where id = '${MINE}' returning id`);
assert.equal(r.rows.length, 1);
ok('the down migration returns the database to the state base-schema.sql declares');

await db.exec(up);
await rejects(() => as('authenticated', MINE,
  `update public.farmers set is_verified = true where id = '${MINE}'`),
  /permission denied/, 'and 0024 can be applied again after that rollback');

console.log(`\n${passed} farmer self-write assertions passed`);
