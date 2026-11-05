/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0028 (one person, one role) against a real PostgreSQL engine.
//
// The owner's complaint is specific: "there should be a clear distinction between
// which type of user one person is — they shouldn't create a farmer account and
// then create a buyer account." Measured in this repo, nothing enforces that.
// update_user_profile() in app.py writes every profile to `farmers`; no path creates a
// `buyers` row; and the role the app believes comes from GoTrue user_metadata.role
// (_auth_role in app.py), which the signed-in person can rewrite.
//
// This file runs the real chain — 0025, then 0026, then 0028 — and proves three
// things the design rested on:
//   1. the hole is open before 0028 and shut after it, for `service_role` as well
//      as for the browser, which is why the guard is a SECURITY DEFINER trigger and
//      not an RLS policy (a bypassrls role steps over a policy; it cannot step over
//      a trigger);
//   2. closing it does not disturb the writes that already work — creating your own
//      profile, and saving it again;
//   3. 0026's owner-insert policy, standing alone, let a person sign themselves up
//      as verified, because the INSERT privilege covered every column.
//
// Run:  cd tests/sql && npm install && node one_role_per_person.test.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const read = (f) => readFileSync(new URL(f, dir), 'utf8');
const guard = read('0025_identity_checks_all_users.up.sql');
const signup = read('0026_self_signup_insert_policies.up.sql');
const up = read('0028_one_role_per_person.up.sql');
const down = read('0028_one_role_per_person.down.sql');

const db = new PGlite();

// The live project as the 2026-11-05 census measured it: base-schema columns,
// RLS enabled on all four tables, the nine policies that exist, and the
// anon=arwdDxtm grants the same query reported.
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
  -- 0002:28-34. user_id is the PRIMARY KEY, which is why "one role row per
  -- person" needs no new mechanism — only a column to hold the role.
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

// Asserts the SQLSTATE as well as the text. The API maps on the code, so a guard
// that raised the right words with the wrong number would be a silent behaviour
// change one layer up.
const rejectsCode = async (fn, code, re, name) => {
  const e = await fn().then(() => null, (err) => err);
  assert.ok(e, `expected ${code}, but the statement succeeded`);
  assert.equal(e.code, code, `wrong SQLSTATE (got ${e.code}): ${e.message}`);
  assert.match(e.message, re);
  ok(name);
};

const MINE  = '22222222-2222-2222-2222-222222222222';   // signs up as a farmer
const DUAL  = '44444444-4444-4444-4444-444444444444';   // the two-role person
const FRESH = '55555555-5555-5555-5555-555555555555';   // a clean new account
const NEWB  = '66666666-6666-6666-6666-666666666666';   // a new buyer
const OPS   = '99999999-9999-9999-9999-999999999999';   // verified by an operator
const ROLL  = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';   // used after the rollback

await db.exec(`
  insert into public.farmers (id, full_name, phone, district)
    values ('${MINE}', 'Julius Jesus Mukasa', '+256771000001', 'Mukono');
  insert into public.farmers (id, full_name, phone, district)
    values ('${DUAL}', 'Sarah Auma', '+256771000002', 'Kasese');
`);

// ── 1. The hole, measured before it is closed ───────────────────────────────
// Nothing in 0025 or 0026 stops one person holding rows in two role tables, and
// the backend writes with the service-role key — so the account the owner is
// describing is simply allowed.
await db.exec(guard);
await db.exec(signup);

r = await as('service_role', null,
  `insert into public.buyers (id, full_name, phone)
     values ('${DUAL}', 'Sarah Auma', '+256771000012') returning id`);
assert.equal(r.rows.length, 1);
ok('before 0028 the same person can be a farmer AND a buyer (this is the complaint)');

r = await as('service_role', null,
  `insert into public.transporters (supabase_uid, full_name, phone, status)
     values ('${DUAL}', 'Sarah Auma', '+256771000016', 'active')`);
assert.equal(r.rows.length, 0);
ok('…and can be given a transporter row as a third, with no rule in the way');

// ── 2. Apply 0028 ───────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0028 is safe to re-run');

r = await db.query(`select c.relname as t, t.tgname, p.prosecdef, p.proconfig
                     from pg_trigger t
                     join pg_class c on c.oid = t.tgrelid
                     join pg_proc p on p.oid = t.tgfoid
                    where not t.tgisinternal and t.tgname = 'one_role_per_person'
                    order by 1`);
assert.deepEqual(r.rows.map(x => x.t), ['buyers', 'farmers', 'transporters']);
assert.deepEqual([...new Set(r.rows.map(x => x.prosecdef))], [true]);
ok('the guard is installed on all three role tables and is SECURITY DEFINER');

r = await db.query(`select column_name, is_nullable
                     from information_schema.columns
                    where table_schema = 'public' and table_name = 'user_roles'
                      and column_name = 'business_role'`);
assert.equal(r.rows.length, 1);
assert.equal(r.rows[0].is_nullable, 'YES');
ok('user_roles gained a nullable business_role (existing admin rows are untouched)');

// ── 3. The guard binds every role, including the one RLS cannot reach ───────
// The contrast this stage rests on. Measured first (in the probe that preceded
// this file) as: an equivalent NOT EXISTS inside an INSERT policy is refused for
// `authenticated` and ignored for `service_role`.
await rejectsCode(() => as('service_role', null,
  `insert into public.buyers (id, full_name, phone)
     values ('${MINE}', 'Julius Jesus Mukasa', '+256771000003')`),
  'GP001', /already registered as a farmer/,
  'service_role cannot open a second role either — a trigger, not a policy');

await rejectsCode(() => as('authenticated', MINE,
  `insert into public.buyers (id, full_name, phone)
     values ('${MINE}', 'Julius Jesus Mukasa', '+256771000004')`),
  'GP001', /already registered as a farmer/,
  '…nor can the browser, with its own token and its own id');

// 0026's buyers_owner_insert would have allowed that row: id IS auth.uid(). The
// trigger is the only thing in the database that says "not a second role".
await rejects(() => as('anon', null,
  `insert into public.farmers (full_name, phone, district)
     values ('Ghost Account', '+256771000005', 'Kampala')`),
  /permission denied for table farmers/,
  '0026’s anon revoke still holds after 0028 (privilege, not policy, now refuses it)');

// ── 4. What already works keeps working ─────────────────────────────────────
// A brand-new person creating their own profile row — the statement
// saveProfileWithRetry in static/index.html causes the backend to run.
r = await as('authenticated', FRESH,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${FRESH}', 'Peter Okello', '+256771000006', 'Wakiso')
     on conflict (id) do update
       set full_name = excluded.full_name, phone = excluded.phone,
           district = excluded.district`);
assert.equal(r.rows.length, 0);
r = await as('authenticated', FRESH,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${FRESH}', 'Peter Okello', '+256771000006', 'Jinja')
     on conflict (id) do update
       set full_name = excluded.full_name, phone = excluded.phone,
           district = excluded.district`);
assert.equal(r.rows.length, 0);
ok('a new signup still creates its own row, and saving it a second time still works '
   + '(the BEFORE INSERT trigger fires on an upsert too, and must not misfire on your own table)');

// A buyer with no farmer row signs up cleanly. 0025:211 revoked `authenticated`'s
// INSERT on buyers outright, so before this file that write had no route in at all.
r = await as('authenticated', NEWB,
  `insert into public.buyers (id, full_name, phone, district)
     values ('${NEWB}', 'Grace Nabirye', '+256771000007', 'Kampala')`);
assert.equal(r.rows.length, 0);
ok('0028 §4 gives authenticated a column-limited INSERT on buyers, so a real buyer '
   + 'signup is possible for the first time');

// …but the SHAPE the backend currently sends is an upsert, and that half is still
// closed: `on conflict (id) do update set district = …` needs UPDATE on buyers,
// which 0025:211 revoked, and no buyers UPDATE policy exists in the census either.
// Measured rather than assumed, because it decides how the buyer-side API stage has
// to be written — see the consequences section of 0028_one_role_per_person.up.sql.
await rejects(() => as('authenticated', NEWB,
  `insert into public.buyers (id, full_name, phone, district)
     values ('${NEWB}', 'Grace Nabirye', '+256771000007', 'Jinja')
     on conflict (id) do update set district = excluded.district`),
  /permission denied for table buyers/,
  'a buyer PROFILE SAVE (the same upsert the route sends for farmers) is still '
  + 'refused: buyers needs an owner UPDATE policy and update columns, not just insert');

// The census, not a boolean: has_column_privilege() names a COLUMN, so it cannot
// answer "may this role INSERT?". information_schema.column_privileges can, and it
// does not truncate, so the exact number of opened columns is asserted here.
r = await db.query(`select table_name, count(*)::int as n
                      from information_schema.column_privileges
                     where grantee = 'authenticated' and privilege_type = 'INSERT'
                       and table_schema = 'public'
                       and table_name in ('farmers','buyers','transporters')
                     group by 1 order by 1`);
assert.deepEqual(r.rows, [{ table_name: 'buyers', n: 4 }, { table_name: 'farmers', n: 10 }]);
ok('the new privilege is column-scoped: id + the 0025 profile list on farmers (10) '
   + 'and on buyers (4, buyers has fewer of those columns)');

r = await db.query(`select has_table_privilege('authenticated','public.transporters','insert') as t`);
assert.equal(r.rows[0].t, false);
ok('transporters is NOT opened to the browser at all — a transporter stays an '
   + 'account an admin creates (admin_create_transporter), not one you sign up as');

// ── 5. Signing yourself up cannot verify yourself ───────────────────────────
await rejects(() => as('authenticated', NEWB,
  `insert into public.farmers (id, full_name, phone, district, is_verified)
     values ('${NEWB}', 'Grace Nabirye', '+256771000008', 'Kampala', true)`),
  /permission denied/,
  'the INSERT path that 0026 opened can no longer self-award is_verified '
  + '(0025:254 only closed the UPDATE path)');

await rejects(() => as('authenticated', NEWB,
  `insert into public.farmers (id, full_name, phone, district, is_premium, otp_code)
     values ('${NEWB}', 'Grace Nabirye', '+256771000009', 'Kampala', true, '123456')`),
  /permission denied/,
  '…nor is_premium, nor an otp_code the OTP stage has not issued');

r = await as('service_role', null,
  `insert into public.farmers (id, full_name, phone, district, is_verified)
     values ('${OPS}', 'Godfrey Ssebugwawo', '+256771000010', 'Kampala', true)`);
assert.equal(r.rows.length, 0);
ok('an operator still verifies someone, so this is a self-service limit, not a wall');

// ── 6. One role row per person, and its allowed values ──────────────────────
r = await as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role)
     values ('${MINE}', 'user', 'farmer') returning business_role`);
assert.deepEqual(r.rows[0], { business_role: 'farmer' });
ok('a business role can be stored for a person');

// The column must accept exactly the words the app already uses — see
// services/legal.py:41-50 and the signup select (#regRole in index.html) — or the
// first real write through it dies with a constraint violation.
for (const good of ['farmer', 'vendor', 'hotel', 'supplier', 'transporter']) {
  r = await as('service_role', null,
    `insert into public.user_roles (user_id, role, business_role)
       values ('${FRESH}', 'user', '${good}') returning business_role`);
  assert.deepEqual(r.rows[0], { business_role: good });
  await db.exec(`delete from public.user_roles where user_id = '${FRESH}'`);
  ok(`business_role accepts '${good}'`);
}

// 'buyer' is what the owner asked to keep people from switching to, but it is not a
// role in this app: buyers are hotels/restaurants/suppliers, and those are the words
// above. Refusing it here is the honest outcome, not a placeholder for the switch
// guard — the switch guard is the pkey plus the missing grants, tested next.
for (const bad of ['buyer', 'admin', 'superadmin', 'input_seller']) {
  await rejects(() => as('service_role', null,
    `insert into public.user_roles (user_id, role, business_role)
       values ('${FRESH}', 'user', '${bad}')`),
    /user_roles_business_role_chk|check constraint/,
    "business_role refuses '" + bad + "' — buyer_type values, admin privileges and "
    + 'role aliases stored elsewhere are not business roles');
}

await rejectsCode(() => as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role)
     values ('${MINE}', 'user', 'vendor')`),
  '23505', /user_roles_pkey/,
  'and a second role row for that person is refused by the 0002 primary key — '
  + 'one role per account is structural, not a policy. (A valid value: an invalid one '
  + 'would die on the CHECK first and never reach the index.)');

r = await as('authenticated', MINE, `select business_role from public.user_roles`);
assert.equal(r.rows.length, 0);
ok('the role store stays unreadable by the signed-in person: `user_roles_no_public` '
   + 'and 0025:211 still hold, so business_role can only be set by the API or an operator');

await rejects(() => as('authenticated', MINE,
  `update public.user_roles set business_role = 'vendor' where user_id = '${MINE}'`),
  /permission denied|row-level security/,
  '…and cannot be rewritten by them either — with a value the CHECK accepts, so this '
  + 'refusal is the grant/RLS layer and not vocabulary: no self-service role switch '
  + 'through a profile field');

// ── 7. The guard needs no new read grant ────────────────────────────────────
// The second measured reason a trigger beat the policy design: the equivalent
// NOT EXISTS inside farmers_owner_insert runs under the caller's own RLS and needs
// SELECT on buyers, and without that grant every signup died with
// "permission denied for table buyers". The trigger reads as its owner, so it is
// indifferent to what the caller can see.
await db.exec(`revoke select on public.buyers from authenticated`);
const GHOST = '77777777-7777-7777-7777-777777777777';
r = await as('authenticated', GHOST,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${GHOST}', 'Aisha Namutebi', '+256771000011', 'Masaka')`);
assert.equal(r.rows.length, 0);
ok('with authenticated unable to SELECT buyers at all, a new signup still works — '
   + 'a policy subquery would have died here (measured), a trigger does not');
await db.exec(`grant select on public.buyers to authenticated`);

// ── 8. The consequence named in the migration header ────────────────────────
// The admin console creates transporters (admin_create_transporter). Someone not
// signed up yet must still be addable; a person who already has a profile must not
// be handed a second role behind their back.
const TRUCK = '88888888-8888-8888-8888-888888888888';
r = await as('service_role', null,
  `insert into public.transporters (supabase_uid, full_name, phone, status)
     values ('${TRUCK}', 'Godfrey Ssebugwawo', '+256771000017', 'pending')`);
assert.equal(r.rows.length, 0);
ok('an operator still adds a transporter who has no profile row');

await rejectsCode(() => as('service_role', null,
  `insert into public.transporters (supabase_uid, full_name, phone, status)
     values ('${MINE}', 'Julius Jesus Mukasa', '+256771000018', 'active')`),
  'GP001', /already registered as a farmer/,
  '…but not a transporter for someone who is already a farmer: the admin console '
  + 'meets the same rule as the browser (that call site needs the message)');

r = await as('service_role', null,
  `update public.transporters set status = 'active', capacity_kg = 400
     where supabase_uid = '${TRUCK}'`);
assert.equal(r.rows.length, 0);
ok('updating an existing transporter still works — the trigger fires on '
   + 'INSERT or UPDATE OF supabase_uid, and the portal changes neither');

// ── 9. Rollback ─────────────────────────────────────────────────────────────
await db.exec(down);
r = await as('service_role', null,
  `insert into public.buyers (id, full_name, phone)
     values ('${FRESH}', 'Peter Okello', '+256771000013')`);
assert.equal(r.rows.length, 0);
ok('after the rollback a second role is possible again, exactly as it was in §1');

r = await db.query(`select count(*)::int as n from pg_trigger t
                     join pg_class c on c.oid = t.tgrelid
                    where t.tgname = 'one_role_per_person' and not t.tgisinternal`);
assert.equal(r.rows[0].n, 0);
r = await db.query(`select count(*)::int as n from information_schema.columns
                    where table_schema = 'public' and table_name = 'user_roles'
                      and column_name = 'business_role'`);
assert.equal(r.rows[0].n, 0);
ok('the rollback removes the triggers and the business_role column');

// What rolling back costs, stated as a measurement rather than a warning.
r = await as('authenticated', ROLL,
  `insert into public.farmers (id, full_name, phone, district, is_verified)
     values ('${ROLL}', 'Self Awarded', '+256771000014', 'Kampala', true)`);
assert.equal(r.rows.length, 0);
ok('and the down path reopens the self-verification INSERT that §4 closed — '
   + 'recorded so a rollback is a decision, not a discovery');

await db.exec(`delete from public.farmers where id = '${GHOST}'`);
await db.exec(up);
r = await as('authenticated', GHOST,
  `insert into public.farmers (id, full_name, phone, district)
     values ('${GHOST}', 'Aisha Namutebi', '+256771000015', 'Masaka')`);
assert.equal(r.rows.length, 0);
ok('and 0028 can be applied again after that rollback');

console.log(`\n${passed} one-role-per-person assertions passed`);

