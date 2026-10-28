-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0022 — A transporter may read their own account. They may not rewrite it.
--
-- 0014_lockdown_internal_tables.up.sql:47-50 created
--   create policy "transporter update own availability" on public.transporters
--     for update to authenticated using (supabase_uid = auth.uid())
--     with check (supabase_uid = auth.uid())
-- and :55 added `grant select, update on public.transporters to authenticated`.
-- The name promises availability; the SQL is a row predicate. using/with_check
-- decide WHICH ROWS a signed-in transporter may touch, never WHICH COLUMNS, so
-- every writable column of their own row is open to a direct PostgREST PATCH:
--
--   status            'inactive' -> 'active'   (undo an admin deactivation)
--   is_available      false -> true            (appear bookable while on a job)
--   vehicle_type      any text                 (0008 gives it no CHECK constraint;
--                                               it is only a comment listing
--                                               pickup|lorry|motorcycle|minibus|
--                                               van|tractor, so a motorcycle can
--                                               be relabelled a lorry)
--   capacity_kg       any number               (claim a load the vehicle cannot
--                                               carry — a safety claim, not tidiness)
--   district, phone, email, notes, plate_number, full_name
--
-- One column is genuinely protected by the policy as written, and it is worth
-- saying so rather than claiming credit for it: `supabase_uid` cannot be
-- re-pointed, because with_check re-evaluates supabase_uid = auth.uid() against
-- the NEW row. tests/sql/transport_assignment.test.mjs §8 asserts that refusal
-- both before and after this migration. Everything above it is unprotected.
--
-- No transition validation, no audit trail, no admin review — and the last one
-- matters for the portal: 0020's claim_delivery() refuses an inactive
-- transporter, which is only a real control while `status` cannot be edited by
-- the person it restricts. A deactivated driver who can PATCH themselves back to
-- 'active' makes that check decorative.
--
-- Nothing behind this policy is in use. A grep for a client write to
-- /rest/v1/transporters across static/index.html, static/transporter.html and
-- static/logistics.html returns zero matches: availability and profile edits
-- already go through POST /api/transporter/availability and the backend, which
-- holds the service-role key and therefore bypasses RLS unchanged. Reads are
-- untouched, so a transporter still sees their own account. This is the same
-- move 0019 made on orders: drop the row-scoped update policy that was never a
-- column guard, keep the read, and let the API be the only writer.
--
-- Verified in source, not re-probed in production: the policy text above is read
-- from the migration files, and no live project query was run for it during this
-- change. Applying it to lvloaweijidgcuezdnmu is the owner's step.
--
-- Drops one policy and narrows grants. No rows are touched. Safe to re-run.

begin;

drop policy if exists "transporter update own availability" on public.transporters;

-- Defence in depth. Dropping the policy already denies the write under RLS, but
-- 0014 also handed authenticated the table privilege, and a future policy that
-- is written loosely would find it waiting.
revoke update, insert, delete, truncate on public.transporters
  from public, anon, authenticated;
grant select on public.transporters to authenticated;

commit;
