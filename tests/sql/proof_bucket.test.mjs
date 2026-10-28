/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

// Verifies migration 0021 (the private proof-of-delivery bucket) against PGlite.
// Supabase's storage schema is not reachable from a test, so this stubs the two
// tables it uses — storage.buckets and storage.objects — with the same RLS shape
// 0012_uploads_bucket.up.sql leaves in production, including its
// uploads_public_read policy. That makes the important assertion measurable
// rather than argued: an object in the new bucket is invisible to every client
// role, while an object in 'uploads' is still visible to anon, from the same
// query on the same table.
//
// It also proves the rollback guard actually fires. storage.objects cascades on
// bucket deletion, so a plain "drop the bucket" rollback would erase the evidence
// behind confirmed deliveries; the down migration counts first and refuses.
// Run:  cd tests/sql && npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const dir = new URL('../../supabase/migrations/', import.meta.url);
const up = readFileSync(new URL('0021_proof_of_delivery_bucket.up.sql', dir), 'utf8');
const down = readFileSync(new URL('0021_proof_of_delivery_bucket.down.sql', dir), 'utf8');

const db = new PGlite();
await db.exec(`
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;

  create schema storage;
  grant usage on schema storage to anon, authenticated, service_role;

  create table storage.buckets (
    id text primary key,
    name text,
    public boolean default false,
    file_size_limit bigint,
    allowed_mime_types text[]
  );
  -- Same ON DELETE CASCADE shape Supabase ships, which is what makes an
  -- unconditional bucket drop destructive.
  create table storage.objects (
    id uuid primary key default gen_random_uuid(),
    bucket_id text references storage.buckets(id) on delete cascade,
    name text,
    owner text
  );
  alter table storage.objects enable row level security;
  grant all on storage.buckets to anon, authenticated, service_role;
  grant all on storage.objects to anon, authenticated, service_role;

  -- Verbatim from 0012_uploads_bucket.up.sql:29-33.
  insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('uploads','uploads',true,27262976,
            array['image/jpeg','image/png','video/mp4']::text[]);
  create policy uploads_public_read on storage.objects
    for select to public using (bucket_id = 'uploads');
`);

let passed = 0;
const ok = (name) => { passed++; console.log('  ok -', name); };
const as = async (role, sql) => {
  await db.exec(`set role ${role};`);
  try { return await db.query(sql); } finally { await db.exec('reset role;'); }
};
const svc = (sql) => as('service_role', sql);

// A known object in each bucket, so "returns nothing" means RLS worked and not
// merely that the test forgot to put data somewhere.
await svc(`insert into storage.objects (bucket_id, name) values
  ('uploads','listing-photo-1.jpg'), ('uploads','listing-photo-2.jpg')`);

let r = await as('anon', `select count(*)::int c from storage.objects where bucket_id='uploads'`);
assert.equal(r.rows[0].c, 2);
ok('the stub reproduces production: anon can read objects in the public uploads bucket');

// ── Apply ─────────────────────────────────────────────────────────────────────
await db.exec(up);
await db.exec(up);
ok('0021 applies and re-applies without duplicating the bucket');

r = await svc(`select public, file_size_limit, allowed_mime_types
               from storage.buckets where id='proof_of_delivery'`);
assert.equal(r.rows.length, 1);
assert.equal(r.rows[0].public, false);
assert.equal(Number(r.rows[0].file_size_limit), 8388608);
assert.deepEqual([...r.rows[0].allowed_mime_types].sort(), ['image/jpeg', 'image/png', 'image/webp']);
ok('the bucket is private, capped at 8 MiB and accepts photographs only');

r = await svc(`select public, file_size_limit from storage.buckets where id='uploads'`);
assert.equal(r.rows[0].public, true);
assert.equal(Number(r.rows[0].file_size_limit), 27262976);
ok('0021 leaves the existing uploads bucket exactly as 0012 configured it');

// ── Nobody but the backend can see a proof object ─────────────────────────────
await svc(`insert into storage.objects (bucket_id, name) values
  ('proof_of_delivery','d1/recipient-photo.jpg')`);

for (const role of ['anon', 'authenticated']) {
  r = await as(role, `select count(*)::int c from storage.objects
                      where bucket_id='proof_of_delivery'`);
  assert.equal(r.rows[0].c, 0);
}
ok('neither anon nor a signed-in user can read a proof-of-delivery object');

r = await as('anon', `select count(*)::int c from storage.objects where bucket_id='uploads'`);
assert.equal(r.rows[0].c, 2);
ok('the same query still returns uploads rows, so the denial above is the bucket, not a broken test');

await db.exec(`set role authenticated;`);
let denied = null;
try {
  await db.query(`insert into storage.objects (bucket_id, name) values ('proof_of_delivery','forged.jpg')`);
} catch (e) { denied = e; }
await db.exec('reset role;');
assert.ok(denied && denied.code === '42501', `expected 42501, got ${denied && denied.code}`);
ok('a client role cannot upload into the proof bucket directly');

r = await svc(`select count(*)::int c from storage.objects where bucket_id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 1);
ok('the service-role backend still writes it, which is the only path by design');

// A hand-added dashboard policy is cleared on re-run.
await db.exec(`create policy proof_public_read on storage.objects for select to public using (true)`);
await db.exec(up);
r = await as('anon', `select count(*)::int c from storage.objects where bucket_id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 0);
ok('re-running 0021 removes a public read policy someone added in the dashboard');

// ── Rollback guard ────────────────────────────────────────────────────────────
let refused = null;
try { await db.exec(down); } catch (e) { refused = e; }
// The down file opens its own transaction, so the RAISE leaves the session in
// "current transaction is aborted" and every later statement would fail with
// 25P02 until the block is closed. Ending it here is what the app's connection
// pool would do on the same error.
try { await db.exec('rollback;'); } catch { /* already ended */ }
assert.ok(refused, 'the down migration ran without complaint while objects were stored');
assert.equal(refused.code, 'P0001');
assert.match(refused.message, /refusing to drop proof_of_delivery/i);
ok('the rollback refuses to drop the bucket while it still holds delivery evidence');

r = await svc(`select count(*)::int c from storage.buckets where id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 1);
r = await svc(`select count(*)::int c from storage.objects where bucket_id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 1);
ok('the failed rollback destroyed nothing: bucket and object both survive');

await svc(`delete from storage.objects where bucket_id='proof_of_delivery'`);
await db.exec(down);
await db.exec(down);
r = await svc(`select count(*)::int c from storage.buckets where id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 0);
ok('once the bucket is empty the rollback drops it, and re-runs cleanly');

r = await svc(`select count(*)::int c from storage.objects where bucket_id='uploads'`);
assert.equal(r.rows[0].c, 2);
r = await as('anon', `select count(*)::int c from storage.objects where bucket_id='uploads'`);
assert.equal(r.rows[0].c, 2);
ok('the rollback does not disturb the uploads bucket or its public read');

await db.exec(up);
await svc(`insert into storage.objects (bucket_id, name) values ('proof_of_delivery','again.jpg')`);
r = await as('authenticated', `select count(*)::int c from storage.objects
                               where bucket_id='proof_of_delivery'`);
assert.equal(r.rows[0].c, 0);
ok('re-applying 0021 after a rollback restores the private bucket and its denial');

console.log(`\n${passed} proof-of-delivery bucket assertions passed`);
