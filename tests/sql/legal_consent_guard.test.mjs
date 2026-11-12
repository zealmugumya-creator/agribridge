/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0031 (database consent guard) against a real PostgreSQL
// engine (PGlite). The marketplace tables and their `authenticated` write policies
// are reproduced from supabase/base-schema.sql; the legal tables and the recording
// RPC are the REAL 0007 file; 0031 up/down are the REAL files under test.
//
// It proves the design's load-bearing claims:
//   1. applying 0031 with an empty requirement set changes nothing (fail-open);
//   2. once requirements are synced, a signed-in user who has not accepted them is
//      refused on a DIRECT PostgREST write (listings/reviews/community_posts/...);
//   3. the same trigger fires INSIDE a SECURITY DEFINER RPC (the create_order_atomic
//      / accept_offer bypass), because auth.uid() reads the caller's JWT, not the
//      definer role — measured with a stand-in definer RPC;
//   4. the service-role backend (auth.uid() null) is unaffected, and anon is still
//      refused by RLS exactly as before;
//   5. profile bootstrap (farmers INSERT) is NOT guarded, so signup still works;
//   6. role resolution matches app.py: business_role authoritative, privilege ->
//      vendor, profile fallback, unresolvable -> fail closed;
//   7. the non-material floor matches LegalService._satisfied;
//   8. sync is an atomic replace, backend-only; rollback removes everything.
//
// Run:  cd tests/sql && npm install && node legal_consent_guard.test.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const read = (f) => readFileSync(new URL(f, dir), 'utf8');
const legal = read('0007_legal_consent.up.sql');
const up = read('0031_legal_consent_guard.up.sql');
const down = read('0031_legal_consent_guard.down.sql');

const db = new PGlite();
await db.exec(`
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
  create schema auth;
  create table auth.users (id uuid primary key);
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema public, auth to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;

  -- Profile tables (bootstrap fallback + signup), as base-schema / 0014 shape them.
  create table public.farmers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null, phone text not null, district text,
    profile_photo text, created_at timestamptz default now()
  );
  create table public.buyers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null, phone text not null unique,
    buyer_type text default 'individual'
      check (buyer_type in ('individual','vendor','hotel','restaurant','exporter','ngo'))
  );
  create table public.transporters (
    id uuid primary key default gen_random_uuid(),
    supabase_uid uuid unique, full_name text, phone text
  );
  create table public.user_roles (
    user_id uuid primary key,
    role text default 'user',
    business_role text
      check (business_role in ('farmer','vendor','hotel','supplier','transporter','exporter'))
  );

  -- Marketplace tables + the authenticated write policies from base-schema.sql.
  create table public.listings (
    id uuid primary key default gen_random_uuid(),
    farmer_id uuid, crop_name text not null, quantity_kg numeric, price_per_kg numeric,
    district text, created_at timestamptz default now()
  );
  create table public.animal_listings (
    id uuid primary key default gen_random_uuid(),
    farmer_id uuid, name text, price numeric, status text default 'active'
  );
  create table public.supplier_products (
    id uuid primary key default gen_random_uuid(),
    supplier_id uuid, name text not null, price numeric default 0
  );
  create table public.reviews (
    id uuid primary key default gen_random_uuid(),
    order_id uuid, farmer_id uuid not null, buyer_id uuid not null,
    rating integer not null check (rating between 1 and 5), comment text
  );
  create table public.community_posts (
    id uuid primary key default gen_random_uuid(),
    author_name text, content text not null, category text default 'general'
  );
  create table public.orders (
    id uuid primary key default gen_random_uuid(),
    buyer_id uuid, farmer_id uuid, status text default 'pending',
    quantity_kg numeric default 1, price_per_kg numeric default 1000
  );
  create table public.order_offers (
    id uuid primary key default gen_random_uuid(),
    request_id uuid, farmer_id uuid, status text default 'sent'
  );

  do $$ declare t text; begin
    foreach t in array array['farmers','buyers','transporters','user_roles','listings',
      'animal_listings','supplier_products','reviews','community_posts','orders','order_offers']
    loop execute format('alter table public.%I enable row level security', t); end loop;
  end $$;

  create policy listings_owner_insert on public.listings for insert to authenticated with check (auth.uid() = farmer_id);
  create policy listings_owner_update on public.listings for update to authenticated using (auth.uid() = farmer_id) with check (auth.uid() = farmer_id);
  create policy listings_public_select on public.listings for select to public using (true);
  create policy animal_owner_insert on public.animal_listings for insert to authenticated with check (auth.uid() = farmer_id);
  create policy animal_owner_update on public.animal_listings for update to authenticated using (auth.uid() = farmer_id) with check (auth.uid() = farmer_id);
  create policy supplier_owner_insert on public.supplier_products for insert to authenticated with check (auth.uid() = supplier_id);
  create policy supplier_owner_update on public.supplier_products for update to authenticated using (auth.uid() = supplier_id) with check (auth.uid() = supplier_id);
  create policy supplier_owner_delete on public.supplier_products for delete to authenticated using (auth.uid() = supplier_id);
  create policy community_auth_insert on public.community_posts for insert to authenticated with check (true);
  create policy reviews_owner_insert on public.reviews for insert to authenticated with check (auth.uid() = buyer_id);
  create policy orders_owner_select on public.orders for select to authenticated using (auth.uid() = buyer_id or auth.uid() = farmer_id);
  create policy orders_farmer_update on public.orders for update to authenticated using (auth.uid() = farmer_id) with check (auth.uid() = farmer_id);
  create policy farmers_owner_insert on public.farmers for insert to authenticated with check (auth.uid() = id);
  create policy farmers_public_select on public.farmers for select to public using (true);

  grant select, insert, update, delete on public.listings, public.animal_listings,
    public.supplier_products, public.reviews, public.community_posts, public.orders,
    public.order_offers to authenticated;
  grant select, insert on public.farmers to authenticated;
  grant select on public.listings, public.animal_listings, public.supplier_products,
    public.reviews, public.community_posts, public.farmers to anon;
`);

// Real legal schema (0007): legal_document_versions, legal_acceptances, the RPC.
await db.exec(legal);
await db.exec(`grant all on all tables in schema public to service_role;
               grant select on public.legal_document_versions to anon;`);

let passed = 0;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, uid, sql, params) => {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub','${uid ?? ''}',false);`);
  try { return await db.query(sql, params); } finally { await db.exec('reset role;'); }
};
const rejects = async (fn, re, name) => { await assert.rejects(fn, re); ok(name); };

const FARMER = '11111111-1111-1111-1111-111111111111';
const SUPPLIER = '22222222-2222-2222-2222-222222222222';
const ADMIN = '33333333-3333-3333-3333-333333333333';
const NOROLE = '44444444-4444-4444-4444-444444444444';
const H = 'a'.repeat(64);

// ── 1. Applies, and re-applies idempotently ──────────────────────────────────
await db.exec(up); await db.exec(up); ok('0031 applies, and re-applies idempotently');
await db.exec(`grant all on all tables in schema public to service_role;`);

// A stand-in for create_order_atomic: SECURITY DEFINER, granted to authenticated,
// inserts into orders. auth.uid() inside it is still the CALLER's uid — this is the
// exact mechanism the real RPC bypass relies on, measured without loading 0015.
await db.exec(`
  create function public.create_order_stub(p_farmer uuid) returns uuid
  language plpgsql security definer set search_path = public, pg_temp as $$
  declare v_id uuid;
  begin
    insert into public.orders (farmer_id, buyer_id, status)
      values (p_farmer, auth.uid(), 'pending') returning id into v_id;
    return v_id;
  end $$;
  grant execute on function public.create_order_stub(uuid) to authenticated, service_role;
`);

// ── 2. Empty requirement set => guard inactive (applying 0031 changes nothing) ─
await as('service_role', null,
  `insert into public.user_roles (user_id, business_role) values ($1,'farmer')`, [FARMER]);
await as('authenticated', FARMER,
  `insert into public.listings (farmer_id, crop_name, quantity_kg, price_per_kg, district)
   values ($1,'Maize',100,1200,'Kampala')`, [FARMER]);
ok('with no requirements synced, an unaccepted farmer can still list (fail-open)');

// ── 3. Sync requirements, then the direct write is refused until accepted ─────
const reqs = [
  { role: 'farmer', doc_type: 'terms-of-use', version: '1.0.0', last_material_version: '1.0.0', requires_acceptance: 'accept' },
  { role: 'farmer', doc_type: 'farmer-terms', version: '1.0.0', last_material_version: '1.0.0', requires_acceptance: 'accept' },
  { role: 'supplier', doc_type: 'terms-of-use', version: '1.0.0', last_material_version: '1.0.0', requires_acceptance: 'accept' },
  { role: 'supplier', doc_type: 'supplier-terms', version: '1.0.0', last_material_version: '1.0.0', requires_acceptance: 'accept' },
  { role: 'vendor', doc_type: 'terms-of-use', version: '1.0.0', last_material_version: '1.0.0', requires_acceptance: 'accept' },
];
let r = await as('service_role', null, `select public.sync_legal_current_requirements($1::jsonb) n`, [JSON.stringify(reqs)]);
assert.equal(r.rows[0].n, 5); ok('backend syncs the current requirement set (atomic replace)');

// Publish the versions so the real recording RPC will accept them.
await db.exec(`insert into public.legal_document_versions (doc_type,version,title,content_sha256,effective_at)
  values ('terms-of-use','1.0.0','Terms','${H}',now()),
         ('farmer-terms','1.0.0','Farmer','${H}',now()),
         ('supplier-terms','1.0.0','Supplier','${H}',now())
  on conflict (doc_type,version) do nothing`);

await rejects(() => as('authenticated', FARMER,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district) values ($1,'Beans',50,2000,'Kampala')`, [FARMER]),
  /current agreements/, 'direct listing INSERT refused while terms unaccepted');

await as('service_role', null,
  `select public.record_legal_acceptance($1,'terms-of-use','1.0.0',$2,'farmer','login_review','{}')`, [FARMER, H]);
await rejects(() => as('authenticated', FARMER,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district) values ($1,'Beans',50,2000,'Kampala')`, [FARMER]),
  /current agreements/, 'still refused with one of two required documents outstanding');

await as('service_role', null,
  `select public.record_legal_acceptance($1,'farmer-terms','1.0.0',$2,'farmer','login_review','{}')`, [FARMER, H]);
await as('authenticated', FARMER,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district) values ($1,'Beans',50,2000,'Kampala')`, [FARMER]);
ok('listing INSERT allowed once every current requirement is accepted');

// UPDATE path.
await as('authenticated', FARMER,
  `update public.listings set price_per_kg=2100 where farmer_id=$1 and crop_name='Beans'`, [FARMER]);
ok('listing UPDATE allowed when complete');

// ── 4. The trigger fires inside a SECURITY DEFINER RPC (the order bypass) ─────
await as('service_role', null,
  `insert into public.user_roles (user_id, business_role) values ($1,'vendor')`, [NOROLE]);
// NOROLE is a vendor with terms-of-use required and nothing accepted.
await rejects(() => as('authenticated', NOROLE, `select public.create_order_stub($1)`, [FARMER]),
  /current agreements/, 'create_order-style SECURITY DEFINER RPC refused for an unaccepted caller');
await as('service_role', null,
  `select public.record_legal_acceptance($1,'terms-of-use','1.0.0',$2,'vendor','login_review','{}')`, [NOROLE, H]);
r = await as('authenticated', NOROLE, `select public.create_order_stub($1) id`, [FARMER]);
assert.ok(r.rows[0].id); ok('same RPC allowed once the caller has accepted (auth.uid survives definer)');

// orders UPDATE (set_order_status / orders_farmer_update path).
await as('service_role', null,
  `insert into public.user_roles (user_id, business_role) values ($1,'farmer')
   on conflict (user_id) do update set business_role='farmer'`, [SUPPLIER]);
// SUPPLIER will be used as a farmer_id owner of an order; give them a farmer order row.
await db.exec(`insert into public.orders (id, farmer_id, buyer_id, status)
  values ('aaaaaaaa-0000-0000-0000-000000000001', '${SUPPLIER}', '${FARMER}', 'pending')`);
// SUPPLIER has business_role farmer now but no acceptances -> UPDATE refused.
await rejects(() => as('authenticated', SUPPLIER,
  `update public.orders set status='confirmed' where id='aaaaaaaa-0000-0000-0000-000000000001'`),
  /current agreements/, 'orders UPDATE refused for an unaccepted farmer');

// ── 5. Backend (service_role) and anon are unaffected ────────────────────────
await as('service_role', null,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district)
   values ($1,'Backend crop',10,100,'Kampala')`, [NOROLE]);
ok('service-role backend write passes with no end-user JWT (already Flask-gated)');
await rejects(() => as('anon', null,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district) values ($1,'x',1,1,'K')`, [FARMER]),
  /row-level security|permission denied/, 'anon listing write still refused by RLS, as before');

// ── 6. Profile bootstrap is NOT guarded (signup must still work) ─────────────
const NEWUSER = '55555555-5555-5555-5555-555555555555';
await as('authenticated', NEWUSER,
  `insert into public.farmers (id, full_name, phone, district) values ($1,'Grace Nakato','+256771234567','Kampala')`, [NEWUSER]);
ok('farmers INSERT (profile bootstrap) is not gated, even with requirements synced');

// ── 7. Role resolution matches app.py ────────────────────────────────────────
// business_role authoritative over a conflicting profile: SUPPLIER has a farmers
// row? No — give SUPPLIER a farmers row AND business_role supplier; supplier-terms
// is required, farmer-terms is not. Accepting farmer-terms must NOT satisfy them.
await db.exec(`insert into public.farmers (id, full_name, phone) values ('${SUPPLIER}','Sup Person','+256700000000')`);
await as('service_role', null,
  `insert into public.user_roles (user_id, business_role) values ($1,'supplier')
   on conflict (user_id) do update set business_role='supplier'`, [SUPPLIER]);
await as('service_role', null,
  `select public.record_legal_acceptance($1,'farmer-terms','1.0.0',$2,'supplier','login_review','{}')`, [SUPPLIER, H]);
await rejects(() => as('authenticated', SUPPLIER,
  `insert into public.supplier_products (supplier_id,name,price) values ($1,'Seed',5000)`, [SUPPLIER]),
  /current agreements/, 'business_role wins over profile: farmer-terms does not satisfy a supplier');
await as('service_role', null,
  `select public.record_legal_acceptance($1,'terms-of-use','1.0.0',$2,'supplier','login_review','{}')`, [SUPPLIER, H]);
await as('service_role', null,
  `select public.record_legal_acceptance($1,'supplier-terms','1.0.0',$2,'supplier','login_review','{}')`, [SUPPLIER, H]);
await as('authenticated', SUPPLIER,
  `insert into public.supplier_products (supplier_id,name,price) values ($1,'Seed',5000)`, [SUPPLIER]);
ok('supplier write allowed after supplier-terms + terms-of-use accepted');

// privilege admin -> vendor docs (terms-of-use required, not farmer-terms).
await as('service_role', null,
  `insert into public.user_roles (user_id, role, business_role) values ($1,'admin',null)`, [ADMIN]);
await rejects(() => as('authenticated', ADMIN,
  `insert into public.community_posts (author_name,content) values ('Admin','hello')`),
  /current agreements/, 'admin (privilege) is held to vendor docs, refused while unaccepted');
await as('service_role', null,
  `select public.record_legal_acceptance($1,'terms-of-use','1.0.0',$2,'vendor','login_review','{}')`, [ADMIN, H]);
await as('authenticated', ADMIN,
  `insert into public.community_posts (author_name,content) values ('Admin','hello')`);
ok('admin write allowed after the vendor terms-of-use is accepted');

// profile fallback: a farmers row with NO user_roles row resolves to farmer.
const FALLBACK = '66666666-6666-6666-6666-666666666666';
await db.exec(`insert into public.farmers (id, full_name, phone) values ('${FALLBACK}','Fall Back','+256700000001')`);
r = await as('service_role', null, `select public.legal_consent_complete($1) c`, [FALLBACK]);
assert.equal(r.rows[0].c, false); ok('profile fallback resolves a farmer with no role row (and is incomplete)');

// unresolvable role -> fail closed.
const GHOST = '77777777-7777-7777-7777-777777777777';
r = await as('service_role', null, `select public.legal_consent_complete($1) c`, [GHOST]);
assert.equal(r.rows[0].c, false); ok('a uid with no role and no profile fails closed');

// ── 8. Non-material floor matches LegalService._satisfied ────────────────────
await as('service_role', null, `select public.sync_legal_current_requirements($1::jsonb)`, [JSON.stringify([
  { role: 'farmer', doc_type: 'terms-of-use', version: '1.0.1', last_material_version: '1.0.0', requires_acceptance: 'accept' },
])]);
// FARMER already accepted terms-of-use 1.0.0; a non-material correction (floor 1.0.0) is satisfied.
r = await as('service_role', null, `select public.legal_consent_complete($1) c`, [FARMER]);
assert.equal(r.rows[0].c, true); ok('non-material correction: an acceptance at the floor still satisfies');
// A material bump (floor 1.0.1) is NOT satisfied by the 1.0.0 acceptance.
await as('service_role', null, `select public.sync_legal_current_requirements($1::jsonb)`, [JSON.stringify([
  { role: 'farmer', doc_type: 'terms-of-use', version: '1.0.1', last_material_version: '1.0.1', requires_acceptance: 'accept' },
])]);
r = await as('service_role', null, `select public.legal_consent_complete($1) c`, [FARMER]);
assert.equal(r.rows[0].c, false); ok('material bump: the older acceptance no longer satisfies');

// ── 9. Sync is backend-only, atomic, and validates its input ─────────────────
await rejects(() => as('authenticated', FARMER,
  `select public.sync_legal_current_requirements('[]'::jsonb)`),
  /permission denied/, 'authenticated cannot sync requirements');
await rejects(() => as('service_role', null,
  `select public.sync_legal_current_requirements('{"role":"farmer"}'::jsonb)`),
  /JSON array/, 'sync rejects a non-array payload');
await as('service_role', null, `select public.sync_legal_current_requirements($1::jsonb)`, [JSON.stringify(reqs)]);
r = await as('service_role', null, `select count(*)::int c from public.legal_current_requirements`);
assert.equal(r.rows[0].c, 5); ok('sync replaces the whole set (no stale rows left behind)');
await rejects(() => as('authenticated', FARMER, `select public.legal_consent_complete($1)`, [FARMER]),
  /permission denied/, 'the completeness resolver is not callable by clients');

// ── 10. Rollback removes everything it added, and reopens the bypass ─────────
await db.exec(down);
r = await db.query(`select count(*)::int c from information_schema.tables
  where table_schema='public' and table_name='legal_current_requirements'`);
assert.equal(r.rows[0].c, 0); ok('down removes the requirements table');
r = await db.query(`select count(*)::int c from pg_trigger t
  join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and t.tgname='legal_consent_guard'`);
assert.equal(r.rows[0].c, 0); ok('down removes every consent-guard trigger');
// With the guard gone, an unaccepted caller can list again (the documented reopen).
const REOPEN = '88888888-8888-8888-8888-888888888888';
await as('service_role', null, `insert into public.user_roles (user_id,business_role) values ($1,'farmer')`, [REOPEN]);
await as('authenticated', REOPEN,
  `insert into public.listings (farmer_id,crop_name,quantity_kg,price_per_kg,district) values ($1,'x',1,1,'K')`, [REOPEN]);
ok('after rollback the direct write is no longer gated (bypass reopened, as documented)');
await db.exec(up); ok('up works again after rollback');

console.log(`\n${passed} checks passed`);