-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0020 rollback — removes the transport assignment columns, rate table and
-- functions added by 0020_transport_assignment.up.sql.
--
-- Two things this rollback deliberately does NOT do:
--
-- 1. It does not restore `deliveries_public_select`. base-schema.sql:255 created
--    a policy letting any anonymous caller select every delivery row, including
--    driver_phone, vehicle_plate and gps_lat/gps_lng. 0020 dropped it. A
--    mechanical rollback would put that exposure back, which is an active harm
--    rather than a neutral undo, so the policy stays dropped and the reason is
--    recorded here. If a true pre-0020 state is genuinely required, re-create it
--    by hand with full knowledge of what it exposes.
--
-- 2. It does not re-grant client write access to deliveries. 0020 revoked
--    update/insert/delete from public/anon/authenticated because an unguarded
--    client write path into a table whose status feeds a financial view is the
--    same class of hole 0019 closed on orders. That revocation stands.
--
-- Column drops are destructive in principle. In practice 0020 adds no rows and
-- `deliveries` held zero rows in production when this was verified
-- (content-range: */0, 2026-10-27), so there is nothing to lose unless the
-- feature has already been used — in which case export the affected columns
-- before running this. Safe to re-run.

begin;

-- ── functions and rate table ─────────────────────────────────────────────────
drop function if exists public.claim_delivery(uuid, uuid, numeric);
drop function if exists public.transport_quote(text, numeric, numeric, boolean);
drop table if exists public.transport_rate_config;

-- ── policies ─────────────────────────────────────────────────────────────────
drop policy if exists "transporter reads own deliveries" on public.deliveries;
drop policy if exists "deliveries service role full access" on public.deliveries;

-- ── indexes ──────────────────────────────────────────────────────────────────
drop index if exists public.deliveries_one_live_assignment_idx;
drop index if exists public.deliveries_unassigned_idx;
drop index if exists public.deliveries_transporter_idx;

-- ── columns ──────────────────────────────────────────────────────────────────
alter table public.deliveries drop column if exists transporter_id;
alter table public.deliveries drop column if exists assigned_at;
alter table public.deliveries drop column if exists assigned_by;
alter table public.deliveries drop column if exists transport_fee_ugx;
alter table public.deliveries drop column if exists fee_basis;
alter table public.deliveries drop column if exists fee_quote_inputs;
alter table public.deliveries drop column if exists fee_quoted_at;
alter table public.deliveries drop column if exists fee_disclosed_to_buyer;
alter table public.deliveries drop column if exists transporter_offer_ugx;
alter table public.deliveries drop column if exists offer_status;
alter table public.deliveries drop column if exists offer_at;
alter table public.deliveries drop column if exists offer_decided_at;
alter table public.deliveries drop column if exists proof_code_hash;
alter table public.deliveries drop column if exists proof_code_issued_at;
alter table public.deliveries drop column if exists proof_code_matches;
alter table public.deliveries drop column if exists proof_code_attempts;
alter table public.deliveries drop column if exists proof_photo_path;
alter table public.deliveries drop column if exists proof_submitted_at;
alter table public.deliveries drop column if exists proof_verified_by;
alter table public.deliveries drop column if exists proof_verified_at;

commit;
